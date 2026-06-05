from distr.gui.oracle.lifecycle import _center_dialog_position


def test_center_dialog_position_uses_target_screen_origin():
    assert _center_dialog_position(
        screen_geometry=(1920, 0, 1440, 900),
        dialog_size=(420, 180),
    ) == (2430, 360)


def test_center_dialog_position_clamps_dialog_larger_than_screen():
    assert _center_dialog_position(
        screen_geometry=(-1280, 120, 800, 600),
        dialog_size=(1000, 700),
    ) == (-1280, 120)
