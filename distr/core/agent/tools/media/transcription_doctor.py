"""
Transcription Doctor Tool

Preflight/health check tool for transcription backends and dependencies.
Reports which backends are available, which models are installed,
whether ffmpeg is available, and where output will be written.
"""

import logging
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from distr.core.agent.tools.media.video_transcriber import check_transcription_backends

logger = logging.getLogger(__name__)


class TranscriptionDoctorInput(BaseModel):
    """Input schema for transcription doctor tool."""
    check_ffmpeg: bool = Field(default=True, description="Check if ffmpeg is available")
    check_backends: bool = Field(default=True, description="Check transcription backends")


class TranscriptionDoctorTool(BaseTool):
    """Tool for checking transcription system health and availability."""
    
    name: str = "transcription_doctor"
    description: str = (
        "Check transcription system health and availability.\n"
        "Reports:\n"
        "- Which transcription backends are available (AssemblyAI, Whisper.cpp, OpenAI Whisper)\n"
        "- Which models are installed\n"
        "- Whether ffmpeg is available\n"
        "- Configuration status of API keys\n"
        "\n"
        "Use this when user says:\n"
        "- 'Check transcription setup'\n"
        "- 'What transcription backends are available?'\n"
        "- 'Test transcription system'\n"
        "- 'Transcription doctor'\n"
    )
    args_schema: type[BaseModel] = TranscriptionDoctorInput
    
    def _run(self, check_ffmpeg: bool = True, check_backends: bool = True, **kwargs) -> str:
        """Run transcription system health check."""
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            assemblyai_key = settings.get('assemblyai_key', '') if settings.get('assemblyai_enabled', False) else None
            openai_key = settings.get('openai_api_key', '') if settings else None
            
            report = []
            report.append("=== Transcription System Health Check ===\n")
            
            if check_ffmpeg:
                import subprocess
                try:
                    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
                    if result.returncode == 0:
                        # Extract version from first line
                        version_line = result.stdout.decode('utf-8').split('\n')[0]
                        report.append(f"✅ ffmpeg: Available\n   {version_line}\n")
                    else:
                        report.append("❌ ffmpeg: Not working\n")
                except FileNotFoundError:
                    report.append("❌ ffmpeg: Not found\n   Install: brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)\n")
                except Exception as e:
                    report.append(f"❌ ffmpeg: Check failed - {str(e)}\n")
            
            if check_backends:
                report.append("\n--- Transcription Backends ---\n")
                preflight = check_transcription_backends(assemblyai_key=assemblyai_key, openai_key=openai_key)
                
                for backend in preflight['backends']:
                    status = "✅" if backend['available'] else "❌"
                    report.append(f"{status} {backend['name']}: {backend['reason']}\n")
                
                if preflight['has_any_backend']:
                    report.append(f"\n✅ At least one backend is available\n")
                else:
                    report.append(f"\n❌ No backends available. Please install and configure at least one:\n")
                    report.append("   - AssemblyAI: pip install assemblyai and set API key\n")
                    report.append("   - Whisper.cpp: Install whisper.cpp and pywhispercpp\n")
                    report.append("   - OpenAI Whisper: pip install openai and set API key\n")
            
            return "".join(report)
            
        except Exception as e:
            logger.error(f"Transcription doctor error: {e}", exc_info=True)
            return f"Error running health check: {str(e)}"
    
    async def _arun(self, check_ffmpeg: bool = True, check_backends: bool = True, **kwargs) -> str:
        """Async version of _run."""
        return self._run(check_ffmpeg, check_backends)

