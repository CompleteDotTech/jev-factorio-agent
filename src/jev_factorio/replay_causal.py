"""Audit recorded producer references without manufacturing causal identities."""
from __future__ import annotations


def audit_producer(events: list[dict], report) -> None:
    observations, models, actions, plans, frames = {}, {}, {}, {}, {}
    for event in events:
        payload = event["payload"]
        kind = event["event_type"]
        trace = payload.get("trace_id")
        decision = payload.get("decision_id")
        line = event["sequence"]

        def issue(code, message, severity="error"):
            report.add(severity, code, message, line, decision)

        if kind in {"run_started", "run_finished"}:
            continue
        if kind not in {
            "step_started", "step_finished", "step_failed", "observation",
            "observation_validated", "model_request", "model_response",
            "candidate_set_created", "candidate_set_filtered", "decision",
            "plan_committed", "plan_failed", "plan_progress", "precondition_checked",
            "action_prepared", "action_returned", "verification", "pending_expired",
            "connection_preflight_rejected",
            "checkpoint_written", "goal_checked", "goal_completed", "goal_activated",
        }:
            issue("unsupported_causal_event", "Event is preserved but its causal semantics are unknown", "gap")
        identities = ("trace_id", "decision_id", "observation_id", "model_call_id",
                      "action_id", "plan_id", "related_action_id")
        if any(payload.get(name) is not None and (
            not isinstance(payload[name], str) or not payload[name]
        ) for name in identities):
            issue("invalid_causal_identity", "Captured causal identity is not nonempty text")
            continue
        if not isinstance(trace, str) or not trace:
            issue("unknown_causal_scope", "Event has no captured trace identity", "gap")
            continue
        for captured, promoted in ((payload.get("session_id"), event["session_id"]),
                                   (payload.get("factorio_tick"), event["time"]["factorio_tick"])):
            if promoted is not None and captured != promoted:
                issue("context_conflict", "Envelope and captured session or tick disagree")
        for name in ("decision_id", "model_call_id", "action_id", "plan_id"):
            captured = payload.get(name)
            promoted = event["correlation"].get(name)
            if promoted is not None and promoted != captured:
                issue("correlation_conflict", "Envelope and captured identity disagree")
        if not isinstance(decision, str) or not decision:
            issue("unknown_decision", "Event has no captured decision identity", "gap")
            continue
        frame = frames.setdefault((trace, decision), {
            "trace_id": trace, "decision_id": decision, "evidence": [],
            "selection": None, "actions": [], "missing_evidence": [],
        })
        frame["evidence"].append(event)
        observation = payload.get("observation_id")
        model = payload.get("model_call_id")
        action = payload.get("action_id")
        plan = payload.get("plan_id")
        related = payload.get("related_action_id")
        if related is not None and (trace, related) not in actions:
            issue("invalid_related_action", "Related action has no preceding preparation")
        if kind == "observation" and payload.get("status") == "ok":
            if not isinstance(observation, str) or not observation:
                issue("missing_observation_id", "Successful observation lacks identity", "gap")
            elif (trace, observation) in observations:
                issue("duplicate_observation", "Observation identity is reused")
            else:
                observations[trace, observation] = event
        elif observation is not None and (trace, observation) not in observations:
            issue("invalid_observation_reference", "Captured observation reference has no preceding observation")
        if kind == "model_request":
            if not isinstance(model, str) or not model:
                issue("missing_model_id", "Model request lacks identity", "gap")
            elif (trace, model) in models:
                issue("duplicate_model_request", "Model call identity is reused")
            else:
                models[trace, model] = {"request": event, "result": None}
        elif kind == "model_response":
            call = models.get((trace, model))
            if call is None:
                issue("invalid_model_reference", "Model response has no preceding request")
            elif call["result"] is not None:
                issue("duplicate_model_result", "Model call has multiple results")
            elif call["request"]["payload"].get("decision_id") != decision:
                issue("model_decision_conflict", "Model response belongs to another decision")
            else:
                call["result"] = event
        elif kind == "decision":
            if frame["selection"] is not None:
                issue("duplicate_decision", "Decision identity has multiple selections")
            frame["selection"] = event
            if "model_called" in payload and type(payload["model_called"]) is not bool:
                issue("invalid_model_flag", "Captured model-called flag is not boolean")
            if payload.get("model_called"):
                call = models.get((trace, model))
                if call is None or call["result"] is None:
                    issue("invalid_model_reference", "Model-backed selection lacks preceding request/result")
                elif call["request"]["payload"].get("decision_id") != decision:
                    issue("model_decision_conflict", "Selection references another decision's model call")
            issue("unknown_candidate_reference", "Producer records no explicit candidate-set identity", "gap")
        elif kind == "plan_committed":
            definition = payload.get("plan")
            if not isinstance(plan, str) or not isinstance(definition, dict):
                issue("missing_plan_definition", "Plan commitment lacks identity or definition", "gap")
            elif definition.get("id") != plan:
                issue("plan_identity_conflict", "Committed plan identity disagrees with its definition")
            else:
                prior = plans.get((trace, plan))
                if prior is not None and prior["payload"].get("decision_id") == decision:
                    issue("duplicate_plan_commitment", "Decision repeats a plan commitment")
                plans[trace, plan] = event
            selection = frame["selection"]
            if selection is not None and selection["payload"].get("plan_id") != plan:
                issue("plan_selection_conflict", "Committed plan differs from recorded selection")
        elif kind == "action_prepared":
            selection = frame["selection"]
            if selection is not None and payload.get("role") != "mock_clock_advance":
                chosen_action = selection["payload"].get("action")
                if chosen_action is not None and chosen_action != payload.get("action"):
                    issue("action_selection_conflict", "Prepared action differs from recorded selection")
                chosen_plan = selection["payload"].get("plan_id")
                if chosen_plan is not None and chosen_plan != plan:
                    issue("plan_selection_conflict", "Prepared action differs from recorded plan selection")
            if not isinstance(action, str) or not action:
                issue("missing_action_id", "Prepared action lacks identity", "gap")
            elif (trace, action) in actions:
                issue("duplicate_action", "Action identity is reused")
            else:
                captured = {"action_id": action, "prepared": event, "result": None,
                            "verifications": [], "acknowledgment": "unknown", "verified": None}
                actions[trace, action] = captured
                frame["actions"].append(captured)
            commitment = plans.get((trace, plan))
            if plan is not None and commitment is None:
                issue("unknown_plan_origin", "Plan definition may originate outside the captured trace", "gap")
            elif commitment is not None:
                steps = commitment["payload"]["plan"].get("steps")
                index = payload.get("step_index")
                if not isinstance(steps, list) or type(index) is not int:
                    issue("unknown_plan_step", "Captured plan step is unavailable", "gap")
                elif not 0 <= index < len(steps):
                    issue("invalid_plan_step", "Action references an absent plan step")
                elif not isinstance(steps[index], dict):
                    issue("invalid_plan_step", "Captured plan step is not an object")
                elif (steps[index].get("action") != payload.get("action")
                      or (steps[index].get("parameters") or {}) != payload.get("parameters")):
                    issue("plan_step_mismatch", "Prepared action differs from the captured plan step")
        elif kind in {"action_returned", "verification", "pending_expired", "connection_preflight_rejected"}:
            captured = actions.get((trace, action))
            if action is None:
                issue("unknown_action_origin", "No action identity is captured for this evidence", "gap")
                continue
            if captured is None:
                issue("invalid_action_reference", "Captured action reference has no preceding preparation")
                continue
            if captured.get("preflight_rejection") is not None:
                issue("conflicting_preflight_evidence", "Rejected connection has contradictory later action evidence")
                continue
            if kind == "connection_preflight_rejected":
                prepared = captured["prepared"]["payload"]
                result = captured["result"]
                valid = (
                    captured.get("preflight_rejection") is None
                    and not captured["verifications"]
                    and result is not None and result["payload"].get("status") == "error"
                    and payload.get("action") == prepared.get("action") == result["payload"].get("action") == "factory_connect"
                    and result["payload"].get("parameters") == prepared.get("parameters")
                    and payload.get("mutation_started") is False
                    and isinstance(payload.get("code"), str)
                    and payload.get("code") in {
                        "missing_fluid_port", "no_connection_route", "insufficient_connection_materials"}
                    and event.get("session_id") is not None
                    and event["session_id"] == captured["prepared"].get("session_id") == result.get("session_id")
                    and all(prepared.get(key) is not None
                            and payload.get(key) == prepared[key] == result["payload"].get(key)
                            for key in ("decision_id", "plan_id", "step_index", "attempt_id"))
                )
                if not valid:
                    issue("invalid_preflight_rejection", "Connection rejection lacks matching pre-mutation evidence")
                    continue
                captured["preflight_rejection"] = event
                captured["acknowledgment"] = "rejected_before_mutation"
                captured["verified"] = False
                continue
            unknown_plan = False
            if kind in {"verification", "pending_expired"}:
                prepared = captured["prepared"]["payload"]
                if any(payload.get(key) is not None and prepared.get(key) is not None
                       and payload[key] != prepared[key] for key in ("plan_id", "step_index")):
                    issue("verification_plan_conflict", "Action evidence refers to another plan or step")
                    captured["verified"] = None
                    continue
                if any(prepared.get(key) is not None and payload.get(key) is None
                       for key in ("plan_id", "step_index")):
                    unknown_plan = True
                    issue("unknown_verification_plan", "Action evidence lacks captured plan-step continuity", "gap")
            if kind == "action_returned":
                if captured["result"] is not None:
                    issue("duplicate_action_result", "Action has multiple recorded results")
                captured["result"] = event
                captured["acknowledgment"] = "returned" if payload.get("status") == "ok" else "ambiguous"
                prepared = captured["prepared"]["payload"]
                if prepared.get("decision_id") != decision:
                    issue("action_decision_conflict", "Action result belongs to another decision")
                origin_session = captured["prepared"].get("session_id")
                result_session = event.get("session_id")
                if origin_session is None or result_session is None:
                    issue("unknown_action_session", "Action result lacks known session continuity", "gap")
                elif origin_session != result_session:
                    issue("action_session_conflict", "Action result belongs to another session")
                if any(payload.get(key) != prepared.get(key) for key in ("action", "parameters", "plan_id", "step_index")):
                    issue("action_result_conflict", "Action result differs from preparation")
            elif kind == "verification":
                captured["verifications"].append(event)
                verdict = payload.get("verified")
                if verdict is None:
                    issue("unknown_verification", "Producer has no postcondition verdict", "gap")
                elif type(verdict) is not bool:
                    issue("invalid_verification", "Captured verification is not boolean")
                else:
                    captured["verified"] = verdict
                observed = observations.get((trace, observation))
                if observed is None:
                    issue("missing_verification_observation", "Verification has no captured observation", "gap")
                elif observed["sequence"] <= captured["prepared"]["sequence"]:
                    issue("stale_verification", "Verification observation precedes preparation")
                sessions = [captured["prepared"].get("session_id"), event.get("session_id")]
                if observed is not None:
                    sessions.append(observed.get("session_id"))
                known_sessions = {session for session in sessions if session is not None}
                if len(known_sessions) > 1:
                    issue("verification_session_conflict", "Verification crosses captured session boundaries")
                    captured["verified"] = None
                elif any(session is None for session in sessions):
                    issue("unknown_verification_session", "Verification lacks known session continuity", "gap")
                    captured["verified"] = None
                if unknown_plan:
                    captured["verified"] = None
    for captured in actions.values():
        if captured["result"] is None:
            report.add("gap", "unknown_acknowledgment", "Prepared action has no recorded result")
        if not captured["verifications"] and not captured.get("preflight_rejection"):
            report.add("gap", "unverified_action", "Action has no captured verification")
    for call in models.values():
        if call["result"] is None:
            report.add("gap", "unfinished_model_call", "Model request has no recorded result")
    for frame in frames.values():
        starts = [event for event in frame["evidence"] if event["event_type"] == "step_started"]
        terminals = [event for event in frame["evidence"]
                     if event["event_type"] in {"step_finished", "step_failed"}]
        if not starts:
            report.add("gap", "missing_step_start", "Captured decision step has no start event",
                       decision_id=frame["decision_id"])
        if len(starts) > 1 or len(terminals) > 1:
            report.add("error", "duplicate_step_boundary", "Decision step has duplicate lifecycle boundaries",
                       decision_id=frame["decision_id"])
        if starts and starts[0] is not frame["evidence"][0]:
            report.add("error", "late_step_start", "Decision evidence precedes its start",
                       decision_id=frame["decision_id"])
        if terminals and terminals[-1] is not frame["evidence"][-1]:
            report.add("error", "post_terminal_evidence", "Decision evidence follows its terminal event",
                       decision_id=frame["decision_id"])
        if not terminals:
            report.add("gap", "unfinished_step", "Captured decision step has no terminal event",
                       decision_id=frame["decision_id"])
    report.decisions = list(frames.values())
