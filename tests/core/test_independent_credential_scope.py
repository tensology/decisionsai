import pytest
from pathlib import Path
from test_ledger_credential import credential_home
from distr.gui.web.security import provision_ledger_operator_token,ledger_header_authorizes_write,LedgerProvisionError

@pytest.mark.parametrize('path,method',[
('/api/workflows/studio/time-ledger-evil/backups','POST'),
('/api/workflows/studio/time-ledger/backups','DELETE'),
('/api/workflows/studio/time-ledger/unregistered-admin-action','POST'),
('/api/workflows/studio/time-ledger/backups','PATCH'),
])
def test_operator_scope_is_exact_known_post_routes(credential_home,path,method):
    provision_ledger_operator_token(confirm='provision-time-ledger')
    secret=credential_home['ledger'].read_text().strip()
    accepted=ledger_header_authorizes_write(path,method,secret)
    assert not accepted,'Ledger credential unexpectedly authorizes outside the exact documented POST route allowlist.'

def test_operator_storage_cannot_alias_internal_credential(credential_home,monkeypatch):
    monkeypatch.setenv('DECISIONSAI_LEDGER_TOKEN_FILE',str(credential_home['internal']))
    with pytest.raises(LedgerProvisionError):provision_ledger_operator_token(confirm='provision-time-ledger')

def test_new_documented_example_does_not_expose_secret_in_curl_argv():
    text=(Path(__file__).parents[2]/'docs/api.md').read_text()
    section=text.split('\n## Time ledger operator credential\n',1)[1].split('\n## Known gaps\n',1)[0]
    assert '-H "$HDR"' not in section,'New curl example expands the credential into child-process argv.'


def test_concurrent_provision_waits_for_new_file_completion(credential_home,monkeypatch):
    import os,threading
    entered=threading.Event();release=threading.Event();original=os.write;first_errors=[];second_errors=[]
    def paused_write(fd,payload):
        entered.set()
        assert release.wait(timeout=3),'test coordination timed out'
        return original(fd,payload)
    monkeypatch.setattr(os,'write',paused_write)
    def first():
        try:provision_ledger_operator_token(confirm='provision-time-ledger')
        except LedgerProvisionError as exc:first_errors.append(exc.code)
    thread=threading.Thread(target=first);thread.start();assert entered.wait(timeout=3)
    try:
        try:provision_ledger_operator_token(confirm='provision-time-ledger')
        except LedgerProvisionError as exc:second_errors.append(exc.code)
    finally:release.set();thread.join(timeout=3)
    assert not first_errors and not second_errors,'Concurrent caller rejects the legitimate newly-created file before its first write completes.'
