"""Conversation ownership and 'stored answers must not outlive the access that produced them'."""
from app.demo import CANARY_HR
from app.services.rag.prompts import REDACTED_MESSAGE
from conftest import all_prompt_text, ask

HR_Q = "What is the grievance escalation code?"


def convo(client, headers, cid):
    return client.get(f"/api/conversations/{cid}", headers=headers)


def start(client, headers, q=HR_Q):
    r = ask(client, headers, q)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["conversation_id"], r.get_json()["message"]


# ---- ownership ---------------------------------------------------------------------------------
def test_owner_can_read_list_and_continue(client, auth, corpus):
    cid, first = start(client, auth("hr"))
    assert CANARY_HR in first["content"] and first["citations"][0]["filename"] == "hr_leave_policy.md"
    assert first["citations"][0]["location"]  # human-readable location for the UI
    again = ask(client, auth("hr"), "How many days of annual leave are there?", cid)
    assert again.get_json()["conversation_id"] == cid
    msgs = convo(client, auth("hr"), cid).get_json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    listing = client.get("/api/conversations", headers=auth("hr")).get_json()
    assert [c["id"] for c in listing["items"]] == [cid] and listing["items"][0]["title"].startswith("What is the grievance")


def test_other_users_cannot_read_delete_or_post_into_a_conversation(client, auth, corpus):
    cid, _ = start(client, auth("hr"))
    ghost = "00000000-0000-0000-0000-000000000000"
    for who in ("eng", "finance", "employee", "admin"):
        h = auth(who)
        assert convo(client, h, cid).status_code == 404
        assert client.delete(f"/api/conversations/{cid}", headers=h).status_code == 404
        assert ask(client, h, "hello there", cid).status_code == 404
    # indistinguishable from a conversation that never existed
    real = convo(client, auth("eng"), cid).get_json()["error"]["message"]
    fake = convo(client, auth("eng"), ghost).get_json()["error"]["message"]
    assert real == fake
    assert convo(client, auth("hr"), cid).status_code == 200  # owner unaffected by the attempts


def test_conversation_lists_are_private(client, auth, corpus):
    start(client, auth("hr"), HR_Q)
    start(client, auth("eng"), "Describe the payments architecture")
    hr_titles = [c["title"] for c in client.get("/api/conversations", headers=auth("hr")).get_json()["items"]]
    eng_titles = [c["title"] for c in client.get("/api/conversations", headers=auth("eng")).get_json()["items"]]
    assert len(hr_titles) == len(eng_titles) == 1
    assert "grievance" in hr_titles[0] and "payments" in eng_titles[0]
    assert client.get("/api/conversations", headers=auth("employee")).get_json()["total"] == 0


def test_owner_can_delete_conversation(client, auth, corpus):
    cid, _ = start(client, auth("hr"))
    assert client.delete(f"/api/conversations/{cid}", headers=auth("hr")).status_code == 200
    assert convo(client, auth("hr"), cid).status_code == 404


def test_conversation_pagination_is_bounded(client, auth, corpus):
    assert client.get("/api/conversations?per_page=1000", headers=auth("hr")).status_code == 400
    assert client.get("/api/conversations?page=0", headers=auth("hr")).status_code == 400


# ---- stored answers vs. current permissions --------------------------------------------------------
def revoke_hr_policy(client, auth, corpus, roles=("hr",)):
    url = f"/api/documents/{corpus['hr_policy']}/permissions"
    return client.patch(url, json={"allowed_roles": list(roles), "classification": "confidential"}, headers=auth("admin"))


def test_document_acl_revocation_redacts_stored_answers(client, auth, corpus, users):
    # give the engineer access, let them ask, then take it away again
    assert revoke_hr_policy(client, auth, corpus, ("hr", "engineering")).status_code == 200
    cid, msg = start(client, auth("eng"))
    assert CANARY_HR in msg["content"]
    assert CANARY_HR in convo(client, auth("eng"), cid).get_data(as_text=True)  # visible while authorised

    assert revoke_hr_policy(client, auth, corpus, ("hr",)).status_code == 200
    body = convo(client, auth("eng"), cid)
    text = body.get_data(as_text=True)
    assistant = [m for m in body.get_json()["messages"] if m["role"] == "assistant"][0]
    assert CANARY_HR not in text and "hr_leave_policy" not in text
    assert assistant["redacted"] is True and assistant["content"] == REDACTED_MESSAGE and assistant["citations"] == []
    # the user's own question is still theirs to see
    assert any(m["role"] == "user" and "grievance" in m["content"] for m in body.get_json()["messages"])

    # access restored -> the answer is served again (visibility is computed at read time, not stored)
    assert revoke_hr_policy(client, auth, corpus, ("hr", "engineering")).status_code == 200
    assert CANARY_HR in convo(client, auth("eng"), cid).get_data(as_text=True)


def test_role_removal_redacts_stored_answers(client, auth, corpus, users):
    uid = users["employee"].id
    assert client.patch(f"/api/users/{uid}/roles", json={"roles": ["employee", "hr"]}, headers=auth("admin")).status_code == 200
    cid, msg = start(client, auth("employee"))
    assert CANARY_HR in msg["content"]
    assert client.patch(f"/api/users/{uid}/roles", json={"roles": ["employee"]}, headers=auth("admin")).status_code == 200
    text = convo(client, auth("employee"), cid).get_data(as_text=True)
    assert CANARY_HR not in text and "redacted" in text


def test_deleted_document_redacts_stored_answers(client, auth, corpus):
    cid, _ = start(client, auth("hr"))
    assert client.delete(f"/api/documents/{corpus['hr_policy']}", headers=auth("admin")).status_code == 200
    text = convo(client, auth("hr"), cid).get_data(as_text=True)
    assert CANARY_HR not in text and REDACTED_MESSAGE in text


def test_user_grant_revocation_redacts_stored_answers(client, auth, corpus, users):
    url = f"/api/documents/{corpus['budget']}/permissions"
    ok = client.patch(url, json={"allowed_roles": ["finance"], "classification": "confidential",
                                 "user_grants": [users["employee"].id]}, headers=auth("admin"))
    assert ok.status_code == 200
    cid, msg = start(client, auth("employee"), "What is the Reserve budget for Q2?")
    assert "CANARY-FIN-9K2M" in msg["content"] or msg["citations"]  # answered from the granted document
    client.patch(url, json={"allowed_roles": ["finance"], "classification": "confidential", "user_grants": []},
                 headers=auth("admin"))
    text = convo(client, auth("employee"), cid).get_data(as_text=True)
    assert "CANARY-FIN-9K2M" not in text and "finance_budget" not in text


def test_deactivated_user_cannot_read_history(client, auth, corpus, users):
    cid, _ = start(client, auth("hr"))
    client.patch(f"/api/users/{users['hr'].id}", json={"is_active": False}, headers=auth("admin"))
    assert convo(client, auth("hr"), cid).status_code == 401


# ---- history never becomes retrieval context ----------------------------------------------------------
def test_old_answers_do_not_feed_later_prompts(client, auth, corpus, llm, users):
    """A follow-up in the same conversation is answered from *currently authorised* documents only; the
    stored earlier answer (which contained now-revoked content) is never re-sent to the model."""
    assert revoke_hr_policy(client, auth, corpus, ("hr", "engineering")).status_code == 200
    cid, msg = start(client, auth("eng"))
    assert CANARY_HR in msg["content"]
    assert revoke_hr_policy(client, auth, corpus, ("hr",)).status_code == 200
    llm.calls.clear()
    r = ask(client, auth("eng"), "Please repeat the code you just told me", cid)
    assert CANARY_HR not in r.get_data(as_text=True)
    assert CANARY_HR not in all_prompt_text(llm)


def test_one_users_conversation_never_reaches_another_users_prompt(client, auth, corpus, llm):
    start(client, auth("hr"), "What is the grievance escalation code?")
    llm.calls.clear()
    ask(client, auth("eng"), "What is the staging signing key identifier?")
    ask(client, auth("employee"), "What are the working hours?")
    assert llm.calls, "the other users' own questions should have produced model calls"
    assert CANARY_HR not in all_prompt_text(llm) and "grievance" not in all_prompt_text(llm)
