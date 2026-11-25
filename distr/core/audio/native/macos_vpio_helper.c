/*
 * macos_vpio_helper.c — Native macOS VoiceProcessingIO full-duplex audio bridge.
 *
 * Uses Apple's CoreAudio kAudioUnitSubType_VoiceProcessingIO AudioUnit so
 * echo cancellation, MacBook mic-array beamforming, and Voice Isolation run
 * inside CoreAudio at the hardware HAL boundary with exact render/capture
 * sample alignment.
 *
 * Build:
 *   clang -O2 -Wall -Wextra -framework AudioToolbox -framework CoreAudio \
 *         -framework CoreFoundation -lpthread \
 *         macos_vpio_helper.c -o macos_vpio_helper
 *
 * Wire protocol (little-endian framing on stdin / stdout):
 *   Header: [uint8 type][uint32_le payload_len]
 *
 *   stdout (helper -> parent):
 *     0x01 MIC   : 16 kHz mono int16 PCM (640 bytes = 20 ms)
 *     0x02 READY : UTF-8 JSON readiness metadata
 *     0x03 STATS : 3 x float32 LE [buffered_ms, in_rms, out_rms]
 *
 *   stdin (parent -> helper):
 *     0x01 SPK   : 16 kHz mono int16 PCM to play through VoiceProcessingIO
 *     0x02 FLUSH : len=0, immediately drop all queued playback audio
 *     0x03 VOL   : float32 LE volume in [0.0, 1.0]
 *     0x04 QUIT  : len=0, stop AudioUnit and exit cleanly
 */

#include <AudioToolbox/AudioToolbox.h>
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <math.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SAMPLE_RATE         16000
#define CHANNELS            1
#define FRAME_SAMPLES       320                 /* 20 ms @ 16 kHz */
#define FRAME_BYTES         (FRAME_SAMPLES * 2) /* 640 bytes */
#define CAP_RING_SAMPLES    (SAMPLE_RATE * 2)   /* 2s capture SPSC ring */
#define PLAY_RING_SAMPLES   (SAMPLE_RATE * 16)  /* 16s playback SPSC ring */
#define MAX_IO_FRAMES       4096

#define MSG_AUDIO           0x01
#define MSG_READY           0x02
#define MSG_FLUSH           0x02
#define MSG_STATS           0x03
#define MSG_VOL             0x03
#define MSG_QUIT            0x04
#define MSG_VOICE_PROCESSING 0x05

/* macOS 14+ ducking configuration (defined here so older SDKs still compile) */
#ifndef kAUVoiceIOProperty_OtherAudioDuckingConfiguration
#define kAUVoiceIOProperty_OtherAudioDuckingConfiguration 2108
#endif

typedef struct {
    Boolean mEnableAdvancedDucking;
    UInt32  mDuckingLevel; /* 10 = min, 20 = default, 30 = max */
} DecisionsOtherAudioDuckingConfig;

static AudioUnit g_au = NULL;
static atomic_bool g_running = true;

/* Playback SPSC ring buffer (stdin thread writes, render callback reads) */
static int16_t g_play_ring[PLAY_RING_SAMPLES];
static _Atomic uint64_t g_play_write = 0;
static _Atomic uint64_t g_play_read = 0;
static _Atomic uint32_t g_volume_bits = 0x3f800000u; /* 1.0f */

/* Capture SPSC ring buffer (input callback writes, stdout thread reads) */
static int16_t g_cap_ring[CAP_RING_SAMPLES];
static _Atomic uint64_t g_cap_write = 0;
static _Atomic uint64_t g_cap_read = 0;

/* Preallocated scratch buffer for AudioUnitRender on real-time input thread */
static int16_t g_render_scratch[MAX_IO_FRAMES];

/* Live RMS telemetry (scaled x10000 as uint32 for lock-free atomic updates) */
static _Atomic uint32_t g_in_rms_scaled = 0;
static _Atomic uint32_t g_out_rms_scaled = 0;

static float load_volume(void) {
    uint32_t bits = atomic_load_explicit(&g_volume_bits, memory_order_relaxed);
    float v = 1.0f;
    memcpy(&v, &bits, sizeof(v));
    if (v < 0.0f) v = 0.0f;
    if (v > 1.0f) v = 1.0f;
    return v;
}

static void store_volume(float v) {
    if (v < 0.0f) v = 0.0f;
    if (v > 1.0f) v = 1.0f;
    uint32_t bits = 0;
    memcpy(&bits, &v, sizeof(bits));
    atomic_store_explicit(&g_volume_bits, bits, memory_order_relaxed);
}

static bool write_all(int fd, const void *buf, size_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    while (len > 0) {
        ssize_t n = write(fd, p, len);
        if (n <= 0) {
            return false;
        }
        p += (size_t)n;
        len -= (size_t)n;
    }
    return true;
}

static bool read_all(int fd, void *buf, size_t len) {
    uint8_t *p = (uint8_t *)buf;
    while (len > 0) {
        ssize_t n = read(fd, p, len);
        if (n <= 0) {
            return false;
        }
        p += (size_t)n;
        len -= (size_t)n;
    }
    return true;
}

static bool send_packet(uint8_t type, const void *payload, uint32_t len) {
    uint8_t hdr[5];
    hdr[0] = type;
    hdr[1] = (uint8_t)(len & 0xffu);
    hdr[2] = (uint8_t)((len >> 8) & 0xffu);
    hdr[3] = (uint8_t)((len >> 16) & 0xffu);
    hdr[4] = (uint8_t)((len >> 24) & 0xffu);
    if (!write_all(STDOUT_FILENO, hdr, sizeof(hdr))) {
        return false;
    }
    if (len > 0 && payload != NULL) {
        if (!write_all(STDOUT_FILENO, payload, len)) {
            return false;
        }
    }
    return true;
}

/* CoreAudio Render Callback (Bus 0: speaker output) — REAL-TIME THREAD */
static OSStatus render_callback(
    void *inRefCon,
    AudioUnitRenderActionFlags *ioActionFlags,
    const AudioTimeStamp *inTimeStamp,
    UInt32 inBusNumber,
    UInt32 inNumberFrames,
    AudioBufferList *ioData
) {
    (void)inRefCon;
    (void)ioActionFlags;
    (void)inTimeStamp;
    (void)inBusNumber;

    if (ioData == NULL || ioData->mNumberBuffers == 0) {
        return noErr;
    }

    int16_t *out = (int16_t *)ioData->mBuffers[0].mData;
    uint32_t frames = inNumberFrames;
    float vol = load_volume();

    uint64_t r = atomic_load_explicit(&g_play_read, memory_order_relaxed);
    uint64_t w = atomic_load_explicit(&g_play_write, memory_order_acquire);
    uint64_t avail = (w > r) ? (w - r) : 0;
    uint32_t to_read = (avail < frames) ? (uint32_t)avail : frames;

    double sum_sq = 0.0;
    for (uint32_t i = 0; i < to_read; i++) {
        int16_t sample = g_play_ring[(r + i) % PLAY_RING_SAMPLES];
        if (vol != 1.0f) {
            float scaled = (float)sample * vol;
            if (scaled > 32767.0f) scaled = 32767.0f;
            if (scaled < -32768.0f) scaled = -32768.0f;
            sample = (int16_t)scaled;
        }
        out[i] = sample;
        double f = (double)sample / 32768.0;
        sum_sq += f * f;
    }
    if (to_read < frames) {
        memset(out + to_read, 0, (size_t)(frames - to_read) * sizeof(int16_t));
    }

    if (to_read > 0) {
        atomic_store_explicit(&g_play_read, r + to_read, memory_order_release);
    }

    double rms = (frames > 0) ? sqrt(sum_sq / (double)frames) : 0.0;
    uint32_t scaled_rms = (uint32_t)(rms * 10000.0);
    atomic_store_explicit(&g_out_rms_scaled, scaled_rms, memory_order_relaxed);
    return noErr;
}

/* CoreAudio Input Callback (Bus 1: AEC-cleaned microphone) — REAL-TIME THREAD */
static OSStatus input_callback(
    void *inRefCon,
    AudioUnitRenderActionFlags *ioActionFlags,
    const AudioTimeStamp *inTimeStamp,
    UInt32 inBusNumber,
    UInt32 inNumberFrames,
    AudioBufferList *ioData
) {
    (void)inRefCon;
    (void)ioData;

    if (inNumberFrames == 0 || inNumberFrames > MAX_IO_FRAMES) {
        return noErr;
    }

    AudioBufferList abl;
    abl.mNumberBuffers = 1;
    abl.mBuffers[0].mNumberChannels = CHANNELS;
    abl.mBuffers[0].mDataByteSize = inNumberFrames * sizeof(int16_t);
    abl.mBuffers[0].mData = g_render_scratch;

    OSStatus err = AudioUnitRender(
        g_au,
        ioActionFlags,
        inTimeStamp,
        inBusNumber,
        inNumberFrames,
        &abl
    );
    if (err != noErr) {
        return err;
    }

    uint64_t w = atomic_load_explicit(&g_cap_write, memory_order_relaxed);
    uint64_t r = atomic_load_explicit(&g_cap_read, memory_order_acquire);
    uint64_t free_space = (w >= r && (w - r) < CAP_RING_SAMPLES)
                              ? (CAP_RING_SAMPLES - (w - r))
                              : 0;
    uint32_t to_write = (free_space < inNumberFrames) ? (uint32_t)free_space : inNumberFrames;

    double sum_sq = 0.0;
    for (uint32_t i = 0; i < to_write; i++) {
        int16_t sample = g_render_scratch[i];
        g_cap_ring[(w + i) % CAP_RING_SAMPLES] = sample;
        double f = (double)sample / 32768.0;
        sum_sq += f * f;
    }
    if (to_write > 0) {
        atomic_store_explicit(&g_cap_write, w + to_write, memory_order_release);
    }

    double rms = (to_write > 0) ? sqrt(sum_sq / (double)to_write) : 0.0;
    uint32_t scaled_rms = (uint32_t)(rms * 10000.0);
    atomic_store_explicit(&g_in_rms_scaled, scaled_rms, memory_order_relaxed);
    return noErr;
}

static AudioDeviceID find_device_by_name(const char *wanted, bool is_input) {
    if (wanted == NULL || wanted[0] == '\0' || strcasecmp(wanted, "System Default") == 0) {
        return kAudioObjectUnknown;
    }

    AudioObjectPropertyAddress prop = {
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 data_size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &prop, 0, NULL, &data_size) != noErr || data_size == 0) {
        return kAudioObjectUnknown;
    }
    UInt32 count = data_size / sizeof(AudioDeviceID);
    AudioDeviceID *devices = (AudioDeviceID *)malloc(data_size);
    if (!devices) return kAudioObjectUnknown;

    AudioDeviceID found = kAudioObjectUnknown;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &prop, 0, NULL, &data_size, devices) == noErr) {
        for (UInt32 i = 0; i < count; i++) {
            /* Check channel count in requested direction */
            AudioObjectPropertyAddress cfg_prop = {
                kAudioDevicePropertyStreamConfiguration,
                is_input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput,
                kAudioObjectPropertyElementMain
            };
            UInt32 cfg_size = 0;
            if (AudioObjectGetPropertyDataSize(devices[i], &cfg_prop, 0, NULL, &cfg_size) != noErr || cfg_size == 0) {
                continue;
            }
            AudioBufferList *bl = (AudioBufferList *)malloc(cfg_size);
            if (!bl) continue;
            UInt32 chans = 0;
            if (AudioObjectGetPropertyData(devices[i], &cfg_prop, 0, NULL, &cfg_size, bl) == noErr) {
                for (UInt32 b = 0; b < bl->mNumberBuffers; b++) {
                    chans += bl->mBuffers[b].mNumberChannels;
                }
            }
            free(bl);
            if (chans == 0) continue;

            CFStringRef name_ref = NULL;
            UInt32 name_size = sizeof(name_ref);
            AudioObjectPropertyAddress name_prop = {
                kAudioObjectPropertyName,
                kAudioObjectPropertyScopeGlobal,
                kAudioObjectPropertyElementMain
            };
            if (AudioObjectGetPropertyData(devices[i], &name_prop, 0, NULL, &name_size, &name_ref) == noErr && name_ref) {
                char buf[256];
                if (CFStringGetCString(name_ref, buf, sizeof(buf), kCFStringEncodingUTF8)) {
                    if (strcasecmp(buf, wanted) == 0 || strcasestr(buf, wanted) != NULL) {
                        found = devices[i];
                        CFRelease(name_ref);
                        break;
                    }
                }
                CFRelease(name_ref);
            }
        }
    }
    free(devices);
    return found;
}

static void *stdin_thread_main(void *arg) {
    (void)arg;
    uint8_t hdr[5];
    uint8_t *payload = NULL;
    size_t payload_cap = 0;

    while (atomic_load_explicit(&g_running, memory_order_relaxed)) {
        if (!read_all(STDIN_FILENO, hdr, sizeof(hdr))) {
            atomic_store_explicit(&g_running, false, memory_order_release);
            break;
        }
        uint8_t type = hdr[0];
        uint32_t len = (uint32_t)hdr[1]
                     | ((uint32_t)hdr[2] << 8)
                     | ((uint32_t)hdr[3] << 16)
                     | ((uint32_t)hdr[4] << 24);

        if (len > 4 * 1024 * 1024) {
            fprintf(stderr, "vpio_helper: oversized packet %u\n", len);
            atomic_store_explicit(&g_running, false, memory_order_release);
            break;
        }

        if (len > payload_cap) {
            uint8_t *next = (uint8_t *)realloc(payload, len);
            if (!next) {
                atomic_store_explicit(&g_running, false, memory_order_release);
                break;
            }
            payload = next;
            payload_cap = len;
        }
        if (len > 0 && !read_all(STDIN_FILENO, payload, len)) {
            atomic_store_explicit(&g_running, false, memory_order_release);
            break;
        }

        if (type == MSG_AUDIO) {
            uint32_t samples = len / 2;
            const int16_t *src = (const int16_t *)payload;
            uint32_t written = 0;
            while (written < samples && atomic_load_explicit(&g_running, memory_order_relaxed)) {
                uint64_t w = atomic_load_explicit(&g_play_write, memory_order_relaxed);
                uint64_t r = atomic_load_explicit(&g_play_read, memory_order_acquire);
                uint64_t used = (w >= r) ? (w - r) : 0;
                uint64_t free_space = (used < PLAY_RING_SAMPLES) ? (PLAY_RING_SAMPLES - used) : 0;
                if (free_space == 0) {
                    usleep(5000);
                    continue;
                }
                uint32_t chunk = samples - written;
                if ((uint64_t)chunk > free_space) {
                    chunk = (uint32_t)free_space;
                }
                for (uint32_t i = 0; i < chunk; i++) {
                    g_play_ring[(w + i) % PLAY_RING_SAMPLES] = src[written + i];
                }
                atomic_store_explicit(&g_play_write, w + chunk, memory_order_release);
                written += chunk;
            }
        } else if (type == MSG_FLUSH) {
            uint64_t w = atomic_load_explicit(&g_play_write, memory_order_acquire);
            atomic_store_explicit(&g_play_read, w, memory_order_release);
            atomic_store_explicit(&g_out_rms_scaled, 0, memory_order_relaxed);
        } else if (type == MSG_VOL && len == 4) {
            float v = 1.0f;
            memcpy(&v, payload, sizeof(v));
            store_volume(v);
        } else if (type == MSG_VOICE_PROCESSING) {
            /* payload[0] = 1 enables Apple voice processing (AEC + Voice
             * Isolation); 0 bypasses it so the output sounds normal. */
            UInt32 bypass = (len >= 1 && payload[0]) ? 0 : 1;
            if (g_au != NULL) {
                AudioUnitSetProperty(g_au, kAUVoiceIOProperty_BypassVoiceProcessing,
                                     kAudioUnitScope_Global, 0, &bypass, sizeof(bypass));
            }
        } else if (type == MSG_QUIT) {
            atomic_store_explicit(&g_running, false, memory_order_release);
            break;
        }
    }

    free(payload);
    return NULL;
}

static void handle_sig(int sig) {
    (void)sig;
    atomic_store_explicit(&g_running, false, memory_order_release);
}

int main(int argc, char **argv) {
    const char *in_dev_name = NULL;
    const char *out_dev_name = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--input-device") == 0 && i + 1 < argc) {
            in_dev_name = argv[++i];
        } else if (strcmp(argv[i], "--output-device") == 0 && i + 1 < argc) {
            out_dev_name = argv[++i];
        }
    }

    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, handle_sig);
    signal(SIGINT, handle_sig);

    AudioComponentDescription desc;
    memset(&desc, 0, sizeof(desc));
    desc.componentType = kAudioUnitType_Output;
    desc.componentSubType = kAudioUnitSubType_VoiceProcessingIO;
    desc.componentManufacturer = kAudioUnitManufacturer_Apple;

    AudioComponent comp = AudioComponentFindNext(NULL, &desc);
    if (!comp) {
        fprintf(stderr, "vpio_helper: VoiceProcessingIO component not found\n");
        return 1;
    }

    OSStatus err = AudioComponentInstanceNew(comp, &g_au);
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: AudioComponentInstanceNew failed (%d)\n", (int)err);
        return 1;
    }

    UInt32 enable_io = 1;
    err = AudioUnitSetProperty(g_au, kAudioOutputUnitProperty_EnableIO,
                               kAudioUnitScope_Input, 1, &enable_io, sizeof(enable_io));
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: EnableIO input failed (%d)\n", (int)err);
        return 1;
    }
    err = AudioUnitSetProperty(g_au, kAudioOutputUnitProperty_EnableIO,
                               kAudioUnitScope_Output, 0, &enable_io, sizeof(enable_io));
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: EnableIO output failed (%d)\n", (int)err);
        return 1;
    }

    /* Optional device selection (best-effort; falls back to system default on error) */
    AudioDeviceID out_id = find_device_by_name(out_dev_name, false);
    if (out_id != kAudioObjectUnknown) {
        OSStatus derr = AudioUnitSetProperty(g_au, kAudioOutputUnitProperty_CurrentDevice,
                                             kAudioUnitScope_Global, 0, &out_id, sizeof(out_id));
        if (derr != noErr) {
            fprintf(stderr, "vpio_helper: output device '%s' rejected (%d), using default\n",
                    out_dev_name, (int)derr);
        }
    }
    AudioDeviceID in_id = find_device_by_name(in_dev_name, true);
    if (in_id != kAudioObjectUnknown && in_id != out_id) {
        OSStatus derr = AudioUnitSetProperty(g_au, kAudioOutputUnitProperty_CurrentDevice,
                                             kAudioUnitScope_Global, 1, &in_id, sizeof(in_id));
        if (derr != noErr) {
            fprintf(stderr, "vpio_helper: input device '%s' rejected (%d), using default\n",
                    in_dev_name, (int)derr);
        }
    }

    /* 16 kHz mono signed 16-bit PCM on both capture output and playback input */
    AudioStreamBasicDescription fmt;
    memset(&fmt, 0, sizeof(fmt));
    fmt.mSampleRate = (Float64)SAMPLE_RATE;
    fmt.mFormatID = kAudioFormatLinearPCM;
    fmt.mFormatFlags = kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked;
    fmt.mFramesPerPacket = 1;
    fmt.mChannelsPerFrame = CHANNELS;
    fmt.mBitsPerChannel = 16;
    fmt.mBytesPerPacket = CHANNELS * 2;
    fmt.mBytesPerFrame = CHANNELS * 2;

    err = AudioUnitSetProperty(g_au, kAudioUnitProperty_StreamFormat,
                               kAudioUnitScope_Output, 1, &fmt, sizeof(fmt));
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: set capture format failed (%d)\n", (int)err);
        return 1;
    }
    err = AudioUnitSetProperty(g_au, kAudioUnitProperty_StreamFormat,
                               kAudioUnitScope_Input, 0, &fmt, sizeof(fmt));
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: set playback format failed (%d)\n", (int)err);
        return 1;
    }

    /* Apple voice processing (AEC + Voice Isolation) changes the output path,
     * so it starts BYPASSED. The app turns it on only while continuous mode is
     * active (MSG_VOICE_PROCESSING), keeping normal audio untouched for PTT. */
    UInt32 bypass = 1;
    AudioUnitSetProperty(g_au, kAUVoiceIOProperty_BypassVoiceProcessing,
                         kAudioUnitScope_Global, 0, &bypass, sizeof(bypass));
    UInt32 mute_out = 0;
    AudioUnitSetProperty(g_au, kAUVoiceIOProperty_MuteOutput,
                         kAudioUnitScope_Global, 0, &mute_out, sizeof(mute_out));

    /* Avoid aggressive ducking of other macOS apps (macOS 14+, best-effort) */
    DecisionsOtherAudioDuckingConfig duck;
    duck.mEnableAdvancedDucking = false;
    duck.mDuckingLevel = 10; /* min */
    AudioUnitSetProperty(g_au, kAUVoiceIOProperty_OtherAudioDuckingConfiguration,
                         kAudioUnitScope_Global, 0, &duck, sizeof(duck));

    AURenderCallbackStruct in_cb = { input_callback, NULL };
    err = AudioUnitSetProperty(g_au, kAudioOutputUnitProperty_SetInputCallback,
                               kAudioUnitScope_Global, 0, &in_cb, sizeof(in_cb));
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: SetInputCallback failed (%d)\n", (int)err);
        return 1;
    }

    AURenderCallbackStruct out_cb = { render_callback, NULL };
    err = AudioUnitSetProperty(g_au, kAudioUnitProperty_SetRenderCallback,
                               kAudioUnitScope_Input, 0, &out_cb, sizeof(out_cb));
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: SetRenderCallback failed (%d)\n", (int)err);
        return 1;
    }

    err = AudioUnitInitialize(g_au);
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: AudioUnitInitialize failed (%d)\n", (int)err);
        return 1;
    }

    err = AudioOutputUnitStart(g_au);
    if (err != noErr) {
        fprintf(stderr, "vpio_helper: AudioOutputUnitStart failed (%d)\n", (int)err);
        AudioUnitUninitialize(g_au);
        AudioComponentInstanceDispose(g_au);
        return 1;
    }

    const char *ready_json = "{\"status\":\"ready\",\"sample_rate\":16000,\"aec\":\"apple_vpio\"}";
    if (!send_packet(MSG_READY, ready_json, (uint32_t)strlen(ready_json))) {
        atomic_store_explicit(&g_running, false, memory_order_release);
    }

    pthread_t stdin_tid;
    pthread_create(&stdin_tid, NULL, stdin_thread_main, NULL);

    int16_t out_chunk[FRAME_SAMPLES];
    int stats_counter = 0;

    while (atomic_load_explicit(&g_running, memory_order_relaxed)) {
        uint64_t r = atomic_load_explicit(&g_cap_read, memory_order_relaxed);
        uint64_t w = atomic_load_explicit(&g_cap_write, memory_order_acquire);
        uint64_t avail = (w > r) ? (w - r) : 0;

        if (avail >= FRAME_SAMPLES) {
            for (uint32_t i = 0; i < FRAME_SAMPLES; i++) {
                out_chunk[i] = g_cap_ring[(r + i) % CAP_RING_SAMPLES];
            }
            atomic_store_explicit(&g_cap_read, r + FRAME_SAMPLES, memory_order_release);

            if (!send_packet(MSG_AUDIO, out_chunk, FRAME_BYTES)) {
                atomic_store_explicit(&g_running, false, memory_order_release);
                break;
            }

            /* Send telemetry every 5 frames (100 ms) */
            if (++stats_counter >= 5) {
                stats_counter = 0;
                uint64_t pw = atomic_load_explicit(&g_play_write, memory_order_acquire);
                uint64_t pr = atomic_load_explicit(&g_play_read, memory_order_relaxed);
                float stats[3];
                stats[0] = (pw > pr) ? ((float)(pw - pr) * 1000.0f / (float)SAMPLE_RATE) : 0.0f;
                stats[1] = (float)atomic_load_explicit(&g_in_rms_scaled, memory_order_relaxed) / 10000.0f;
                stats[2] = (float)atomic_load_explicit(&g_out_rms_scaled, memory_order_relaxed) / 10000.0f;
                if (!send_packet(MSG_STATS, stats, sizeof(stats))) {
                    atomic_store_explicit(&g_running, false, memory_order_release);
                    break;
                }
            }
        } else {
            usleep(2000); /* 2 ms poll */
        }
    }

    AudioOutputUnitStop(g_au);
    AudioUnitUninitialize(g_au);
    AudioComponentInstanceDispose(g_au);
    pthread_detach(stdin_tid);
    return 0;
}
