class ChatWindowStyles:
    MAIN_WINDOW = """
        QMainWindow {
            background-color: #343541;
        }
        * {
            font-family: Arial, sans-serif;
        }
    """

    LEFT_WIDGET = """
        QWidget {
            background-color: #202123;
            border: none;
        }
    """

    SEARCH_WIDGET = """
        QWidget {
            height: 44px;
            margin: 0;
            padding: 0;
        }
    """

    SEARCH_INPUT = """
        QLineEdit {
            background-color: #202123;
            border: 1px solid #565869;
            border-radius: 6px;
            padding: 40px 5px 40px 5px;
            font-size: 13px;
            color: #ececf1;
            margin: 8px 40px 8px 12px;
            height: 45px;
            line-height: 45px;
        }
        QLineEdit:focus {
            border-color: #565869;
            background-color: #2d2d3a;
        }
        QLineEdit::placeholder {
            color: #8e8ea0;
        }
    """

    SEARCH_ICON = """
        QLabel {
            margin: 8px 10px 0 0;
        }
        QLabel:focus {
            background-color: #2d2d3a;
        }
    """

    CHAT_LIST = """
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
        /* These styles control the selected item appearance when window is not active */
        QListWidget:!active::item:selected,
        QListWidget::item:selected:!active {
            color: #ffffff;
        }
    """

    NEW_CHAT_BUTTON = """
        QPushButton {
            background-color: #0084ff;
            color: white;
            border-radius: 20px;
            font-size: 20px;
            font-weight: bold;
            border: none;
        }
        QPushButton:hover {
            background-color: #0073e6;
        }
    """

    CHAT_THREAD_VIEW = """
        QTextEdit {
            border: none;
            background-color: #343541;
            color: #ececf1;
            padding: 0 20%;
            selection-background-color: #2d2d3a;
            selection-color: #ffffff;
            font-size: 14px;
            line-height: 1.5;
        }
        QTextEdit QScrollBar:vertical {
            width: 8px;
            background: transparent;
        }
        QTextEdit QScrollBar::handle:vertical {
            background: #565869;
            border-radius: 4px;
            min-height: 30px;
        }
        QTextEdit QScrollBar::add-line:vertical,
        QTextEdit QScrollBar::sub-line:vertical {
            height: 0px;
        }
    """

    INPUT_AREA = """
        QTextEdit {
            background-color: #40414f;
            border: 1px solid #565869;
            border-radius: 12px;
            color: #ececf1;
            padding: 12px 40px 12px 16px;
            font-size: 14px;
            line-height: 1.5;
            margin: 0;
        }
        QTextEdit:focus {
            border-color: #565869;
        }
        QTextEdit::placeholder {
            color: #8e8ea0;
        }
        QTextEdit QScrollBar {
            width: 0px;
            height: 0px;
        }
    """

    SEND_BUTTON = """
        QPushButton {
            background-color: transparent;
            border: none;
            border-radius: 6px;
            margin: 0;
            padding: 4px;
            color: #ffffff;
            font-size: 16px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #2d2d3a;
        }
        QPushButton:disabled {
            color: #565869;
        }
        QPushButton:pressed {
            background-color: #202123;
        }
    """

    VOICE_BUTTON = """
        QPushButton {
            background-color: transparent;
            border: none;
            border-radius: 6px;
            margin: 0;
            padding: 4px;
            color: #ffffff;
            font-size: 16px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #2d2d3a;
        }
        QPushButton:disabled {
            color: #565869;
        }
        QPushButton:pressed {
            background-color: #202123;
        }
    """ 