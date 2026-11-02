import time

import pytest

from raj.contracts import (ContractError, canonical_json, generate_signing_key, make_execution_envelope,
                           make_job_token, payload_hash, verify_object)


def test_canonical_json_is_stable_and_rejects_floats():
    assert canonical_json({"b": 1, "a": "ગુજરાત"}) == '{"a":"ગુજરાત","b":1}'.encode()
    with pytest.raises(ContractError):
        canonical_json({"amount": 1.5})


def test_job_token_roundtrip_and_tamper():
    k = generate_signing_key()
    t = make_job_token(k, job_id="j", org_id="o", client_id="c", device_id="d", company_guid="g",
                       capability="read", allowed_tools=["trial_balance"])
    body = verify_object(k.public_key(), "job_token", t)
    assert body["client_id"] == "c" and body["capability"] == "read"
    raw, sig = t.split(".")
    with pytest.raises(ContractError):
        verify_object(k.public_key(), "job_token", raw[:-2] + "AA." + sig)
    with pytest.raises(ContractError):
        verify_object(generate_signing_key().public_key(), "job_token", t)
    with pytest.raises(ContractError):  # kind is bound into the signature
        verify_object(k.public_key(), "execution_envelope", t)


def test_expiry():
    k = generate_signing_key()
    t = make_job_token(k, job_id="j", org_id="o", client_id="c", device_id="d", company_guid="g",
                       capability="read", allowed_tools=[], ttl=1)
    with pytest.raises(ContractError, match="expired"):
        verify_object(k.public_key(), "job_token", t, now=time.time() + 5)


def test_envelope_binds_payload_hash():
    k = generate_signing_key()
    p = {"voucher_type": "Payment", "entries": [{"ledger": "A", "side": "dr", "amount": "1.00"}]}
    e = make_execution_envelope(k, job_id="j", approval_id="a", draft_id="d", draft_version=1, row_id="r",
                                payload=p, company_guid="g", device_id="dev", reviewer_id="u")
    body = verify_object(k.public_key(), "execution_envelope", e)
    assert body["payload_sha256"] == payload_hash(p)
    p2 = {**p, "entries": [{"ledger": "A", "side": "dr", "amount": "10.00"}]}
    assert payload_hash(p2) != body["payload_sha256"]
