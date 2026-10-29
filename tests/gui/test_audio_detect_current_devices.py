from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def test_detect_does_not_present_remembered_disconnected_devices_as_available(monkeypatch):
    from distr.core.audio import utils
    from distr.gui.web.routes.settings.audio import register_routes

    old = {'name':'Disconnected headset','id':'Disconnected headset'}
    current = {'name':'Built-in Speakers','id':'Built-in Speakers'}
    monkeypatch.setattr(utils,'detect_devices',lambda: ([],[],[old,current],[]))
    monkeypatch.setattr(utils,'query_native_devices',lambda: ([current],[]))
    from unittest.mock import Mock
    refresh=Mock(return_value=[])
    monkeypatch.setattr('distr.core.agent.config_loader._query_devices_fresh_subprocess',refresh)
    router=APIRouter()
    register_routes(router,None)
    app=FastAPI()
    app.include_router(router,prefix='/api')
    result=TestClient(app).post('/api/audio/detect')
    assert result.status_code == 200
    assert result.json()['success']
    refresh.assert_called_once_with(force_refresh=True)
    assert [d['name'] for d in result.json()['output_devices']] == ['System Default','Built-in Speakers']
