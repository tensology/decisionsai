"""Portable incoming follow-up policy, kept identical in Tensology and DecisionsAI."""
import re

POLICY_VERSION = 'incoming-followup-v1'
_FOLLOWUP = re.compile(r'\b(any idea|how (?:did|has|this|that)|why (?:did|does|is|was|has)|following up|follow[ -]?up|still (?:not|broken|wrong)|happened|same (?:issue|problem)|thanks.{0,30}\bbut)\b', re.I)
_STOP = set('the a an this that it is was are be to of and in on for with please investigate check request customer reply update about how why did has any idea happened from not still'.split())


def _tokens(text):
    return set(re.findall(r'[a-z0-9]{3,}', str(text or '').lower())) - _STOP


def rank_followups(proposal, candidates):
    """Rank related work in the exact project and source conversation.

    Conversation identity alone never merges unrelated work. Topic overlap or
    explicit follow-up language is required; best score then recency wins.
    """
    text = ' '.join(str(proposal.get(k) or '') for k in ('title', 'description'))
    bodies = ' '.join(str(row.get('body') or '') for row in proposal.get('evidence') or []
                      if isinstance(row, dict) and not row.get('context_only') and row.get('direction') != 'outbound')
    followup = bool(_FOLLOWUP.search(text + ' ' + bodies))
    tokens = _tokens(text + ' ' + bodies)
    context = set(map(str, proposal.get('source_context_message_ids') or []))
    out = []
    for candidate in candidates:
        if (candidate.get('project_id') != proposal.get('project_id')
                or candidate.get('source') != proposal.get('source')
                or not proposal.get('source_thread_id')
                or candidate.get('source_thread_id') != proposal['source_thread_id']):
            continue
        previous = set(map(str, candidate.get('message_ids') or []))
        overlap = len(tokens & _tokens(str(candidate.get('title') or '') + ' ' + str(candidate.get('description') or '')))
        references = len(context & previous)
        if not followup and overlap < 2:
            continue
        score = references * 100 + overlap * 10 + int(followup)
        out.append({**candidate, 'match_score': score, 'match_policy': POLICY_VERSION,
                    'match_reason': 'source context' if references else 'same conversation and topic' if overlap >= 2 else 'follow-up in same conversation'})
    return sorted(out, key=lambda row: (row['match_score'], str(row.get('created_at') or ''), int(row.get('thread_id') or row.get('id') or 0)), reverse=True)
