(function () {
    'use strict';

    function dispose(element) {
        if (!element) return;
        var video = element._skinPreviewVideo || (element.matches('video') ? element : null);
        if (!video) return;
        if (video._skinFrameId && video.cancelVideoFrameCallback) {
            video.cancelVideoFrameCallback(video._skinFrameId);
        }
        if (video._skinRafId) cancelAnimationFrame(video._skinRafId);
        video.pause();
        video.removeAttribute('src');
        video.load();
    }

    function create(url, chromaKey, chromaThreshold, cssText, cap = 256) {
        var video = document.createElement('video');
        video.src = url;
        video.autoplay = true;
        video.muted = true;
        video.loop = true;
        video.setAttribute('playsinline', '');

        if (!Array.isArray(chromaKey) || chromaKey.length !== 3) {
            video.style.cssText = cssText;
            return video;
        }

        var wrapper = document.createElement('span');
        wrapper.dataset.skinVideoPreview = 'true';
        wrapper.style.cssText = cssText + '; display:block; position:relative; overflow:hidden;';

        var canvas = document.createElement('canvas');
        canvas.width = 256;
        canvas.height = 256;
        // object-fit keeps the frame's own aspect ratio. Without it a non-square
        // container stretched the square canvas and the skin looked squashed.
        canvas.style.cssText = 'display:block; width:100%; height:100%; object-fit:contain;';
        wrapper.appendChild(canvas);

        // Match the backing store to the source video's aspect ratio, so the
        // letterbox maths below is a no-op and the chroma key samples real corners.
        function syncCanvasAspect() {
            var vw = video.videoWidth;
            var vh = video.videoHeight;
            if (!vw || !vh) return;
            var factor = Math.min(cap / vw, cap / vh, 1);
            var nextWidth = Math.max(1, Math.round(vw * factor));
            var nextHeight = Math.max(1, Math.round(vh * factor));
            if (nextWidth !== canvas.width || nextHeight !== canvas.height) {
                canvas.width = nextWidth;
                canvas.height = nextHeight;
            }
        }
        video.addEventListener('loadedmetadata', syncCanvasAspect);
        video.addEventListener('loadeddata', syncCanvasAspect);

        // Keep the source video in the document so Safari continues decoding it,
        // but never expose its green source frame to the user.
        video.style.cssText = 'position:absolute; width:1px; height:1px; opacity:0; pointer-events:none;';
        wrapper.appendChild(video);
        wrapper._skinPreviewVideo = video;

        var context = canvas.getContext('2d', { willReadFrequently: true });
        var threshold = Math.max(0, Number(chromaThreshold) || 30);
        var softEdge = Math.max(1, threshold * 0.6);
        var thresholdSquared = threshold * threshold;
        var softLimit = threshold + softEdge;
        var softLimitSquared = softLimit * softLimit;
        var keyR = Number(chromaKey[0]) || 0;
        var keyG = Number(chromaKey[1]) || 0;
        var keyB = Number(chromaKey[2]) || 0;
        var sampledDecodedKey = false;

        function drawFrame() {
            if (!wrapper.isConnected) return;
            if (video.readyState >= 2) {
                syncCanvasAspect();
                context.clearRect(0, 0, canvas.width, canvas.height);
                var sourceWidth = video.videoWidth || canvas.width;
                var sourceHeight = video.videoHeight || canvas.height;
                var scale = Math.min(canvas.width / sourceWidth, canvas.height / sourceHeight);
                var drawWidth = sourceWidth * scale;
                var drawHeight = sourceHeight * scale;
                var drawX = (canvas.width - drawWidth) / 2;
                var drawY = (canvas.height - drawHeight) / 2;
                context.drawImage(video, drawX, drawY, drawWidth, drawHeight);
                var frame = context.getImageData(0, 0, canvas.width, canvas.height);
                var pixels = frame.data;
                if (!sampledDecodedKey) {
                    var lastPixel = (canvas.width * canvas.height - 1) * 4;
                    var bottomLeft = canvas.width * (canvas.height - 1) * 4;
                    var topRight = (canvas.width - 1) * 4;
                    keyR = Math.round((pixels[0] + pixels[topRight] + pixels[bottomLeft] + pixels[lastPixel]) / 4);
                    keyG = Math.round((pixels[1] + pixels[topRight + 1] + pixels[bottomLeft + 1] + pixels[lastPixel + 1]) / 4);
                    keyB = Math.round((pixels[2] + pixels[topRight + 2] + pixels[bottomLeft + 2] + pixels[lastPixel + 2]) / 4);
                    sampledDecodedKey = true;
                }
                for (var i = 0; i < pixels.length; i += 4) {
                    var dr = pixels[i] - keyR;
                    var dg = pixels[i + 1] - keyG;
                    var db = pixels[i + 2] - keyB;
                    var distanceSquared = dr * dr + dg * dg + db * db;
                    if (distanceSquared <= thresholdSquared) {
                        pixels[i + 3] = 0;
                    } else if (distanceSquared <= softLimitSquared) {
                        var distance = Math.sqrt(distanceSquared);
                        pixels[i + 3] = Math.round(255 * (distance - threshold) / softEdge);
                        if (keyG - Math.max(keyR, keyB) >= 40) {
                            pixels[i + 1] = Math.min(pixels[i + 1], Math.max(pixels[i], pixels[i + 2]));
                        }
                    }
                }
                context.putImageData(frame, 0, 0);
            }
            if (video.requestVideoFrameCallback) {
                video._skinFrameId = video.requestVideoFrameCallback(drawFrame);
            } else {
                video._skinRafId = requestAnimationFrame(drawFrame);
            }
        }

        video.addEventListener('loadeddata', drawFrame, { once: true });
        video.play().catch(function() {});
        return wrapper;
    }

    window.DecisionsSkinVideo = { create, dispose };
})();
