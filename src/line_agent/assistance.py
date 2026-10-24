"""Generation-bound classification and reply drafts. This module cannot send LINE."""
from __future__ import annotations

import re
import time
import unicodedata
from uuid import uuid4

from .assistance_context import assemble_context, digest, source_current
from .assistance_model import AssistanceFailure, implementation, validate_result

CATEGORIES = {"仕事", "プライベート", "両方", "不明"}
SAFE_CONTEXT_ERRORS = {
    "context_source_changed", "context_snapshot_duplicate", "context_identity_missing",
    "context_identity_ambiguous", "context_kind_mismatch", "context_prior_missing",
    "context_source_count_mismatch", "context_incoming_missing", "context_message_too_large",
    "context_budget_exceeded",
    "context_uncheckpointed_wal", "context_owner_not_corroborated", "context_owner_binding_changed",
}


def live_signature(store, chat):
    job = store.get("classification_job", chat) or {}
    return digest({"messages": store.records("message", scope=chat),
        "deliveries": store.records("delivery", scope=chat),
        "job_trigger": {k: job.get(k) for k in ("latest_message_id", "updated_at")},
        "memory": store.get("memory", chat),
        "source": store.get("setting", "assistance_source"),
        "consent": store.get("setting", "assistance_consent"),
        "businesses": store.get("setting", "assistance_businesses")})


def _san_name(name):
    ordinary = {"たくさん", "皆さん", "みなさん", "おじさん", "おばさん", "お父さん",
                "お母さん", "父さん", "母さん"}
    for match in re.finditer("さん", name):
        end = match.end()
        if end < len(name) and unicodedata.category(name[end])[0] not in {"P", "S", "Z"}:
            continue
        prefix = name[:match.start()].rstrip()
        bounds = list(re.finditer(r"[\s|｜/／,、【】\[\]（）()]", prefix))
        start = bounds[-1].end() if bounds else 0
        quote = name[start:end]
        if "".join(quote.split()) not in ordinary and any(
            unicodedata.category(c)[0] in {"L", "N"} for c in prefix[start:]
        ):
            return True
    return False


def classification(document, prediction):
    """Owner truth > approved strong purpose rules > prior; new purpose stays independent."""
    prior = document.get("prior") or {}
    memory = document.get("owner_memory") or {}
    owner = prior.get("owner_confirmed") is True
    fixed = prior.get("category") if owner else None
    if memory.get("confidence") == "owner_corrected":
        owner = True
        labels = set(memory.get("labels") or []) & CATEGORIES
        fixed = ("両方" if {"仕事", "プライベート"} <= labels else
                 next(iter(labels)) if len(labels) == 1 else "不明")
    strong = []
    name = document["display_name"]
    if _san_name(name):
        strong.append("honorific_san")
    if document["kind"] == "group" and any(x in name for x in ("おつなぎ", "お繋ぎ", "お繫ぎ")):
        strong.append("group_referral_name")
    if any(b["name"] in name for b in document["businesses"]):
        strong.append("registered_business")
    if prediction["referral_evidence"]:
        strong.append("referral")
    if owner:
        category, source, reason = fixed, "owner_correction", "本人が訂正した分類を維持。"
    elif strong:
        descriptions = {"honorific_san": "LINE名のさん付け", "group_referral_name": "紹介のグループ名",
                        "registered_business": "本人確認済みの事業名", "referral": "人の紹介の会話"}
        category, source, reason = "仕事", "approved_priority_rules", (
            "・".join(descriptions[r] for r in strong) + "を根拠に仕事と判定。")
    elif prior.get("category") in CATEGORIES and prior["category"] != "不明":
        category, source, reason = prior["category"], "prior_classification", prior["reason"]
    elif prediction["category_basis"] == "name":
        category, source, reason = "仕事", "name_semantics_proposal", prediction["reason"]
    elif document["coverage"] == "all_acquired":
        category, source, reason = prediction["category"], "current_acquired_context", prediction["reason"]
    else:
        category, source, reason = "不明", "history_review_required", "全文再分析が未実施のため関係性を保留。"
    return {"category": category, "source": source, "reason": reason, "rule_ids": strong,
        "suggested_category": prediction["category"], "relationship": prediction["relationship"],
        "suggested_category_basis": prediction["category_basis"],
        "name_evidence": prediction["name_evidence"],
        "current_purpose": prediction["purpose"], "current_reason": prediction["reason"],
        "evidence": prediction["evidence"], "uncertainty": prediction["uncertainty"],
        "relationship_review_suggested": prediction["category"] not in {category, "不明"},
        "owner_confirmed": owner}


def _consent(store):
    value = store.get("setting", "assistance_consent") or {}
    if value.get("provider") != "OpenAI Codex CLI" or value.get("scope") != "incoming_classification_drafts":
        raise AssistanceFailure("external_ai_not_authorized")
    source = store.get("setting", "assistance_source")
    identity = (store.get("setting", "identity") or {}).get("mid")
    if (not source or not identity or value.get("source_digest") != digest(source)
        or value.get("identity_index") != store._id("consent_owner", identity)):
        raise AssistanceFailure("external_ai_not_authorized")


def process_pending(service, provider, limit=3):
    if not 1 <= limit <= 10:
        raise ValueError("assistance limit must be 1..10")
    _consent(service.store)
    counts = {"completed": 0, "stale": 0, "failed": 0, "deferred": 0}
    with service.store.exclusive("assistance", blocking=False):
        pending = [r["value"] for r in service.store.records("classification_job")
                   if r["value"].get("state") in {"pending", "error"}]
        pending.sort(key=lambda j: j["updated_at"])
        processed = 0
        for job in pending:
            if processed >= limit:
                break
            if service.paused():
                break
            chat = job["chat"]
            operation_id = uuid4().hex[:16]
            job_key = digest({"chat": chat, "message": job["latest_message_id"],
                              "updated_at": job["updated_at"], "provider": provider.metadata})
            attempt = service.store.get("assistance_attempt", job_key) or {"attempts": 0}
            if attempt.get("state") == "running":
                # Holding the exclusive assistance lock proves the previous writer
                # is gone. A killed generation never becomes a completed result.
                recovered = {**attempt, "state": "interrupted", "reason": "assistance_interrupted",
                             "stage": "recovery", "detected_at": time.time()}
                with service.store.atomic() as tx:
                    tx.put("assistance_failure", attempt.get("operation_id") or uuid4().hex,
                           recovered, scope=chat)
                    tx.put("assistance_attempt", job_key, recovered)
                    if attempt["attempts"] >= 2 and tx.get("classification_job", chat) == job:
                        tx.put("classification_job", chat, {**job, "state": "error",
                            "reason": "assistance_interrupted", "failure_stage": "recovery",
                            "operation_id": attempt.get("operation_id"),
                            "next_check": "retry_errors_after_runtime_recovery"}, scope=chat)
                attempt = recovered
            if attempt["attempts"] >= 2 or attempt.get("retry_after", 0) > time.time():
                counts["deferred"] += 1
                continue
            budget = service.store.get("setting", "assistance_budget") or {"requests": []}
            times = [t for t in budget["requests"] if t > time.time()-3600]
            if len(times) >= 30:
                counts["deferred"] += 1
                continue
            processed += 1
            stage = "source_fetch"
            try:
                # Refresh a bounded recent window via read-only SDK calls. This also
                # permits exact self-message corroboration for newly received chats.
                service.read_messages(chat, 100)
                signature = live_signature(service.store, chat)
                stage = "source_context"
                document = assemble_context(service.store, chat)
                trigger = next(m for m in document["messages"] if m["ref"] == document["trigger_ref"])
                if document["trigger_decryption_failed"] or trigger["text_state"] == "decryption_failed":
                    raise AssistanceFailure("message_decryption_failed", {
                        "next_check": "e2ee_readiness_and_message_recovery"})
                if signature != live_signature(service.store, chat):
                    counts["stale"] += 1
                    continue
                generation = digest({"document": document, "provider": provider.metadata,
                    "job": job_key, "live_signature": signature, "operation_id": operation_id})
                service.store.put("assistance_attempt", job_key, {"attempts": attempt["attempts"]+1,
                    "operation_id": operation_id, "state": "running", "started_at": time.time()})
                service.store.put("setting", "assistance_budget", {"requests": times+[time.time()]})
                stage = "model_inference"
                _consent(service.store)
                response = provider.infer(document)
                stage = "model_validation"
                prediction = validate_result(response["result"], document)
                value = {"generation": generation, "chat": chat, "display_name": document["display_name"],
                    "classification": classification(document, prediction),
                    "draft": {"status": prediction["draft_status"], "text": prediction["text"],
                        "reason": prediction["draft_reason"], "questions": prediction["questions"],
                        "evidence": prediction["draft_evidence"], "approval": "not_approved"},
                    "context": document, "provider": response["provider"], "usage": response["usage"],
                    "tool_calls": response["tool_calls"], "operation_id": operation_id,
                    "created_at": time.time(), "expires_at": time.time()+86400,
                    "live_signature": signature, "job_key": job_key}
                if response["tool_calls"] != 0:
                    raise AssistanceFailure("model_tool_call_rejected")
                stage = "result_commit"
                # Signature and queue generation must match inside the write lock.
                with service.store.atomic() as tx:
                    current = tx.get("classification_job", chat)
                    # live_signature reads a separate read connection under the SQLite write lock.
                    stale = (current != job or signature != live_signature(service.store, chat)
                             or service.paused())
                    value["state"] = "stale" if stale else "ready"
                    tx.put("assistance_result", generation, value, scope=chat)
                    if not stale:
                        tx.put("current_assistance", chat, {"generation": generation,
                                                          "created_at": value["created_at"]})
                        tx.put("classification_job", chat, {**job, "state": "complete",
                            "generation": generation, "completed_at": time.time()}, scope=chat)
                    tx.put("assistance_attempt", job_key, {"attempts": attempt["attempts"]+1,
                        "operation_id": operation_id, "state": value["state"]})
                counts["stale" if stale else "completed"] += 1
            except Exception as error:
                reason = error.reason if isinstance(error, AssistanceFailure) else (
                    str(error) if type(error) is ValueError and str(error) in SAFE_CONTEXT_ERRORS
                    else "assistance_unexpected_failure")
                with service.store.atomic() as tx:
                    current = tx.get("classification_job", chat)
                    if current == job:
                        tx.put("classification_job", chat, {**job, "state": "error",
                            "reason": reason, "operation_id": operation_id, "failure_stage": stage,
                            "details": error.details if isinstance(error, AssistanceFailure) else {},
                            "next_check": stage + "_contract_and_dependencies",
                            "retry_after": time.time()+60}, scope=chat)
                    tx.put("assistance_attempt", job_key, {"attempts": attempt["attempts"]+1,
                        "operation_id": operation_id, "state": "error", "reason": reason,
                        "details": error.details if isinstance(error, AssistanceFailure) else {},
                        "stage": stage, "next_check": stage + "_contract_and_dependencies",
                        "retry_after": time.time()+60})
                counts["failed"] += 1
    return counts


def retry_failed(service, provider):
    """Explicit owner CLI recovery; preserve the failure audit and hourly request budget."""
    _consent(service.store)
    reset = 0
    with service.store.exclusive("assistance", blocking=False):
        for row in service.store.records("classification_job"):
            job = row["value"]
            if job.get("state") != "error":
                continue
            key = digest({"chat": job["chat"], "message": job["latest_message_id"],
                          "updated_at": job["updated_at"], "provider": provider.metadata})
            with service.store.atomic() as tx:
                if tx.get("classification_job", job["chat"]) != job:
                    continue
                old = tx.get("assistance_attempt", key)
                tx.put("assistance_retry", uuid4().hex, {"previous_attempt": old,
                    "previous_job": job, "requested_at": time.time()}, scope=job["chat"])
                tx.put("assistance_attempt", key, {"attempts": 0})
                clean = {k: job[k] for k in ("chat", "latest_message_id", "updated_at")}
                tx.put("classification_job", job["chat"], {**clean, "state": "pending"}, scope=job["chat"])
                reset += 1
    return reset


def refresh_stale(service):
    """Explicit CLI refresh after a policy/source update; never refresh daily by itself."""
    _consent(service.store)
    count = 0
    with service.store.exclusive("assistance", blocking=False):
        for row in service.store.records("classification_job"):
            job = row["value"]
            if job.get("state") != "complete" or draft_get(service.store, job["chat"])["state"] != "stale":
                continue
            with service.store.atomic() as tx:
                if tx.get("classification_job", job["chat"]) != job:
                    continue
                tx.put("assistance_refresh", uuid4().hex, {"previous_job": job,
                    "requested_at": time.time()}, scope=job["chat"])
                tx.put("classification_job", job["chat"], {k: v for k, v in
                    {**job, "state": "pending", "updated_at": time.time(),
                     "received_updated_at": job.get("received_updated_at", job["updated_at"])}.items()
                    if k not in {"generation", "completed_at"}},
                    scope=job["chat"])
                count += 1
    return count


def run_assistance(service, provider, watch=False, interval=15, limit=3):
    if not 5 <= interval <= 3600:
        raise ValueError("assistance interval must be 5..3600")
    totals = {"completed": 0, "stale": 0, "failed": 0, "deferred": 0}
    try:
        while not service.paused():
            service.store.put("setting", "assistance_runtime", {"state": "processing",
                "checked_at": time.time(), "provider": provider.metadata})
            values = process_pending(service, provider, limit)
            for key, count in values.items():
                totals[key] += count
            service.store.put("setting", "assistance_runtime", {"state": "idle",
                "checked_at": time.time(), "last_batch": values, "provider": provider.metadata})
            if not watch:
                break
            time.sleep(interval)
    finally:
        old = service.store.get("setting", "assistance_runtime") or {}
        service.store.put("setting", "assistance_runtime", {**old, "state": "stopped",
            "stopped_at": time.time()})
    return totals


def _source_changed(store):
    try:
        configured_source = store.get("setting", "assistance_source")
        if configured_source:
            source_current(configured_source)
        return False
    except (OSError, ValueError):
        return True


def draft_get(store, chat, *, source_changed=None):
    job = store.get("classification_job", chat) or {}
    processing = {k: job.get(k) for k in ("state", "reason", "operation_id", "failure_stage",
                                        "details", "next_check", "retry_after") if k in job}
    pointer = store.get("current_assistance", chat)
    if not pointer:
        return {"chat": chat, "state": job.get("state", "not_analyzed"), "reason": job.get("reason"),
                "processing": processing}
    value = store.get("assistance_result", pointer["generation"])
    if source_changed is None:
        source_changed = _source_changed(store)
    stale = (value["expires_at"] <= time.time() or
             source_changed or
             value["provider"].get("implementation") != implementation() or
             live_signature(store, chat) != value["live_signature"])
    result = {k: value[k] for k in ("chat", "display_name", "generation", "classification", "draft",
                                   "provider", "created_at", "expires_at", "state")}
    context = value["context"]
    result["processing"] = processing
    result["coverage"] = {k: context[k] for k in ("available_count", "input_count", "coverage",
        "phone_history_complete", "media_originals_viewed")}
    if stale:
        result.update(state="stale", draft={"status": "stale", "text": None,
                      "reason": "会話・本人訂正・出所の更新、または期限切れ。再生成が必要。",
                      "approval": "not_approved"})
    return result


def drafts_list(store, offset=0, limit=20):
    if not 0 <= offset or not 1 <= limit <= 100:
        raise ValueError("invalid draft pagination")
    pointers = store.records("current_assistance")
    pointers.sort(key=lambda r: (r["value"].get("created_at", 0), r["id"]), reverse=True)
    page = pointers[offset:offset+limit]
    changed = _source_changed(store) if page else False
    items = [draft_get(store, r["id"], source_changed=changed) for r in page]
    return {"total": len(pointers), "items": items,
            "next_offset": offset+limit if offset+limit < len(pointers) else None}
