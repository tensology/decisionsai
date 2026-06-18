"""Startup PTY shell argv must not use login (-l) mode (drops -c on Rosetta)."""

from distr.core.terminal import _native_pty_command


def test_native_pty_command_uses_arm64_and_no_login_flag():
    args = _native_pty_command("/bin/zsh", "cd ./frontend/; npm run dev;")
    assert args[0] == "arch"
    assert args[1] == "-arm64"
    assert "/bin/zsh" in args
    assert "-l" not in args
    assert "-c" in args
    joined = " ".join(args)
    assert "source ~/.zshrc" in joined
    assert "npm run dev" in joined
