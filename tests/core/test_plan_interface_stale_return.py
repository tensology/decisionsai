from distr.core.planning.interface_rebuild import mark_flows, overlay, summary_text


SOURCE = 'screen "Customer" id=customer route="/customers"\n#| My page description.\n  heading "Original body"\n'
SIDE = '# decisions-flow: {"v":1,"nodes":{"customer":{"note":"Keep my note"}}}\n'


def test_route_disappears_then_returns_clears_only_automatic_label_and_keeps_annotations():
    original = mark_flows(SOURCE) + SIDE
    gone, counts = overlay(original, '', revision=2)
    assert 'Customer (stale)' in gone and '# @stale scan:/customers' in gone
    assert counts['stale'] == 1
    repeated, _ = overlay(gone, '', revision=3)
    assert repeated == gone
    returned, counts = overlay(gone, SOURCE.replace('Original body', 'New scanner body'), revision=4)
    assert 'screen "Customer"' in returned and '(stale)' not in returned
    assert 'Original body' in returned and 'New scanner body' not in returned
    assert '#| My page description.' in returned and returned.endswith(SIDE)
    assert returned.count('screen ') == 1 and 'id=customer' in returned
    assert '# @generated scan:/customers' in returned
    assert counts['restored'] == 1 and '1 returned routes reconciled.' in summary_text(counts)


def test_human_body_edit_while_absent_is_preserved_without_generated_ownership():
    gone, _ = overlay(mark_flows(SOURCE) + SIDE, '', revision=2)
    edited = gone.replace('Original body', 'My edited body')
    returned, _ = overlay(edited, SOURCE, revision=3)
    assert 'Customer (stale)' not in returned and 'My edited body' in returned
    assert '# @generated' not in returned and '# @stale' not in returned
    assert returned.endswith(SIDE)
    again, _ = overlay(returned, SOURCE, revision=4)
    assert again == returned and again.count('screen ') == 1


def test_literal_or_legacy_ambiguous_stale_labels_are_not_guessed():
    literal = SOURCE.replace('"Customer"', '"Customer (stale)"')
    gone, _ = overlay(mark_flows(literal), '', revision=2)
    returned, _ = overlay(gone, SOURCE, revision=3)
    assert 'screen "Customer (stale)"' in returned
    legacy, _ = overlay(literal, SOURCE, revision=4)
    assert 'screen "Customer (stale)"' in legacy
    assert legacy.count('screen ') == 1


def test_human_label_edit_while_absent_is_not_reinterpreted_as_automatic_flag():
    gone, _ = overlay(mark_flows(SOURCE), '', revision=2)
    edited = gone.replace('"Customer (stale)"', '"My chosen (stale)"')
    returned, _ = overlay(edited, SOURCE, revision=3)
    assert '"My chosen (stale)"' in returned and returned.count('screen ') == 1
    assert '# @generated' not in returned and '# @stale' not in returned
