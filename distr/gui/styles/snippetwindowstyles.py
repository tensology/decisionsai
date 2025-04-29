class SnippetWindowStyles:
    # Base styles
    MAIN_WINDOW = """
        QMainWindow {
            background-color: #343541;
        }
        * {
            font-family: Arial, sans-serif;
        }
    """

    LEFT_PANEL = """
        QWidget {
            background-color: #202123;
        }
    """

    SEARCH_INPUT = """
        QLineEdit {
            background-color: #40414f;
            border: 1px solid #565869;
            border-radius: 6px;
            padding: 8px 12px;
            padding-right: 30px;
            font-size: 13px;
            color: #ececf1;
            margin: 0;
        }
        QLineEdit:focus {
            border: 1px solid #565869;
            background-color: #2d2d3a;
            outline: none;
        }
        QLineEdit::placeholder {
            color: #8e8ea0;
        }
    """

    SNIPPET_LIST = """
        QListWidget {
            background-color: transparent;
            border: none;
            outline: none;
            padding: 5px;
        }
        QListWidget::item {
            background-color: transparent;
            height: 32px;
            padding-left: 12px;
            padding-right: 12px;
            border-radius: 4px;
            margin: 2px 4px;
            color: #ffffff;
            font-size: 13px;
        }
        QListWidget::item:selected {
            background-color: #343541;
        }
        QListWidget::item:hover:!selected {
            background-color: #2d2d3a;
        }
        QListWidget:!active::item:selected,
        QListWidget::item:selected:!active {
            color: #ffffff;
        }
    """

    BUTTON = """
        QPushButton {
            background-color: #2d2d3a;
            color: #ececf1;
            border: 1px solid #565869;
            border-radius: 4px;
            padding: 8px;
            font-size: 13px;
        }
        QPushButton:hover {
            background-color: #363648;
        }
        QPushButton:pressed {
            background-color: #3d3d4f;
        }
    """

    PRIMARY_BUTTON = """
        QPushButton {
            background-color: #40414f;
            color: #ececf1;
            border: 1px solid #565869;
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 13px;
        }
        QPushButton:hover {
            background-color: #2d2d3a;
        }
    """

    CODE_EDITOR = """
        QsciScintilla {
            background-color: #2d2d3a;
            color: #ececf1;
            border: 1px solid #565869;
            border-radius: 4px;
            selection-background-color: #3d3d4f;
            selection-color: #ececf1;
        }
    """

    TAG = """
        QLabel {
            background-color: #3d3d4f;
            color: #ececf1;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 12px;
        }
    """

    TAG_LIST = """
        QListWidget {
            background-color: #40414f;
            border: 1px solid #565869;
            border-radius: 6px;
            padding: 8px;
            min-height: 40px;
            max-height: 80px;
        }
    """

    ICON_BUTTON = """
        QPushButton {
            background-color: transparent;
            color: #ececf1;
            border: 1px solid #565869;
            border-radius: 12px;
            font-size: 16px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #2d2d3a;
        }
    """

    LABEL = """
        QLabel {
            color: #ececf1;
            font-size: 13px;
            font-weight: 500;
        }
    """

    TAG_LABEL = """
        QLabel {
            background-color: #2188ff;
            color: #ffffff;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 12px;
        }
    """

    TAG_DELETE_BUTTON = """
        QPushButton {
            background-color: transparent;
            color: #ffffff;
            border: none;
            font-weight: bold;
            padding: 0;
            margin-left: 4px;
        }
        QPushButton:hover {
            color: #ff4d4d;
        }
    """

    RENAME_EDITOR = """
        QLineEdit {
            background-color: #2d2d3a;
            color: #ececf1;
            border: 1px solid #565869;
            border-radius: 4px;
            padding-left: 12px;
            padding-right: 12px;
            font-size: 13px;
        }
        QLineEdit:focus {
            border-color: #565869;
        }
    """

    PRIMARY_BUTTON_SUCCESS = """
        QPushButton {
            background-color: #28a745;
            color: #ececf1;
            border: 1px solid #28a745;
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 13px;
        }
        QPushButton:disabled {
            background-color: #28a745;
            border-color: #28a745;
            color: #ececf1;
        }
    """