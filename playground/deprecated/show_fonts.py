from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFontDatabase
import sys

app = QApplication(sys.argv)
font_db = QFontDatabase()
families = font_db.families(QFontDatabase.WritingSystem.Any)
print("Available fonts:")
for family in sorted(families):
    print(family)

sys.exit(app.exec())