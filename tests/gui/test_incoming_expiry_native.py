"""Native ribbon rendering keeps the existing proposal action available."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtWidgets
from distr.gui.development_hot_corner import DevelopmentHotCornerPanel, _ExpiredProposalRibbon


def test_native_expiry_is_an_overlay_with_an_enabled_action(monkeypatch, tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    preferences = QtCore.QSettings(str(tmp_path / 'preferences.ini'), QtCore.QSettings.Format.IniFormat)
    monkeypatch.setattr(QtCore, 'QSettings', lambda *_args: preferences)
    monkeypatch.setattr('distr.core.workflow.development_sidebar.load_pinned_boards', lambda: [])
    panel = DevelopmentHotCornerPanel(enabled=False)
    panel._project_rows = [(QtWidgets.QTreeWidgetItem(), {'project_id': 7, 'name': 'DecisionsAI'})]
    panel._section_expanded['proposals'] = True
    now = datetime.now(timezone.utc)
    base = {'id': 'native-expired', 'origin': 'local', 'source': 'whatsapp', 'status': 'candidate',
            'title': 'Review the client request and its source evidence', 'project_id': 7,
            'project_name': 'DecisionsAI', 'message_ids': ['original'],
            'evidence': [{'id': 'original', 'sent_at': (now - timedelta(hours=97)).isoformat()}]}
    panel._proposals = [base, {**base, 'id': 'native-pending', 'title': 'Review the current request',
                              'evidence': [{'id': 'original', 'sent_at': now.isoformat()}]}]
    started = []
    monkeypatch.setattr(panel, '_start_proposal', lambda row: started.append(row['id']))
    panel._render_proposals()
    panel.resize(400, 500)
    panel.show()
    panel.tree.expandAll()
    app.processEvents()
    ribbons = panel.findChildren(_ExpiredProposalRibbon)
    assert len(ribbons) == 1
    assert ribbons[0].testAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    buttons = [button for button in panel.findChildren(QtWidgets.QPushButton) if button.text() == 'Create thread']
    assert len(buttons) == 2 and all(button.isEnabled() for button in buttons)
    buttons[0].click()
    assert started == ['native-expired']
    assert panel._proposals[0]['status'] == 'candidate'
    output = Path('/tmp/decisions-incoming-expiry-native.png')
    assert panel.grab().save(str(output))
    panel.close()
    panel.deleteLater()
    app.processEvents()
