"""transfer_routes.py: the routes behind what people see of the transfer
(transfer spec §14, piece T-P12). The words are ui_transfer's; this file reads
the store, checks who is asking, and answers.

    GET  /api/ui/transfer/<uid>          the instrument's Data transfer rows
    GET  /api/results/conflicts          open and recently decided conflicts
    POST /api/results/conflicts/decide   {ref, choice: keep|send, seen?}
    GET  /results/conflicts              the page
    GET  /api/transfer/overview          Settings › Transfer: benches, bridge
    POST /api/transfer/benches/<uid>/reset   revoke a bench's token
    POST /api/transfer/journal-import    road C: a copied journal folder (?dry=1)
    GET  /api/dedupe/approvals           decisions recorded, applied or not
    GET  /instruments/<uid>              only while no other piece serves it

Every read here is of LEM's STORE; LabCore is not a parameter. Every write
needs a signed-in person, is confirmed by reading it back, and answers with
the sentence a person needs when it did not land.

**The server never writes a result (D1).** A decision is a row in
`result_conflict` (who, when, which way); the bench's next sync answer carries
it (`resolutions`), the bench files it through LabStation with the guard
expecting the value the person saw, and journals a `resolution`, which marks
the decision delivered. Nothing on this side touches `samples`,
`sample_tests` or `test_results`.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import ui_transfer

#: A decided conflict stays on the page this long, so the person who pressed
#: the button can watch the bench take it.
DECIDED_DAYS = 7
MAX_IMPORT_BYTES = 64 * 1024 * 1024


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utc()).isoformat(timespec="seconds")


class _Unread(Exception):
    """A store read failed: said, never treated as empty."""


def register(app, store, *, registry, machines: Callable[[], Optional[List[dict]]],
             href: Callable[[str, str], str], authed: Callable[[], bool],
             current_user: Callable[[], str], split: bool = True) -> None:
    """`split` is False when the store and LabCore are one gateway (the
    test suite's usual shape): then nothing on a page render or a live poll
    reads it, because there that read would be a LabCore op."""
    from flask import jsonify, render_template, request

    watch = ui_transfer.TransferWatch(store, enabled=split)
    app.config["TRANSFER_WATCH"] = watch

    def rows(sql: str, args=None) -> List[dict]:
        res = store.read_sql(sql, args or [])
        if not isinstance(res, dict) or res.get("error") or "rows" not in res:
            raise _Unread((res or {}).get("error") if isinstance(res, dict)
                          else "the store did not answer")
        return res["rows"]

    def unread(what: str, exc) -> tuple:
        return jsonify({"error": "LEM could not read %s from its store: %s. "
                                 "Nothing here is a statement about the record; "
                                 "try again." % (what, exc)}), 503

    def deny():
        return jsonify({"error": "Sign in to do that."}), 401

    def titles() -> Dict[str, str]:
        out = {}
        for m in machines() or []:
            out[m["machine_uid"]] = m.get("title") or m["machine_uid"]
        if not out:
            try:
                for r in rows("SELECT machine_uid, title FROM lem_machine_config"):
                    out[str(r["machine_uid"])] = r.get("title") or r["machine_uid"]
            except _Unread:
                pass
        return out

    def known(uid: str) -> Optional[dict]:
        """{machine_uid, title, checking_in} or None when LEM has no such
        instrument. A failed read raises: not knowing is not "no such"."""
        for m in machines() or []:
            if m.get("machine_uid") == uid:
                return {"machine_uid": uid, "title": m.get("title") or uid,
                        "checking_in": bool(m.get("live") or m.get("module_running"))}
        got = rows("SELECT machine_uid, title FROM lem_machine_config WHERE "
                   "machine_uid = ? AND retired_at IS NULL", [uid])
        if got:
            return {"machine_uid": uid, "title": got[0].get("title") or uid,
                    "checking_in": None}
        return None

    # ── the instrument's section ─────────────────────────────────────────
    def section_for(uid: str) -> Optional[dict]:
        m = known(uid)
        if m is None:
            return None
        facts = watch.facts()
        try:
            got = rows("SELECT MAX(filed_at) AS at FROM result_ledger WHERE "
                       "machine_uid = ?", [uid])
            last_filed = (got[0].get("at") if got else None) or ""
        except _Unread:
            last_filed = None
        entry = registry.get(uid)
        ambiguous = recovered = None
        if entry is not None:
            try:
                amb = rows("SELECT COUNT(*) AS n FROM bench_record WHERE "
                           "machine_uid = ? AND kind = 'ambiguity'", [uid])
                ambiguous = int(amb[0]["n"] or 0)
                adopt = rows("SELECT body FROM bench_record WHERE machine_uid = ? "
                             "AND kind = 'adoption'", [uid])
                n = None
                for r in adopt:
                    try:
                        body = json.loads(r.get("body") or "{}")
                    except ValueError:
                        continue
                    got_n = body.get("recovered")
                    if isinstance(got_n, int) and not isinstance(got_n, bool):
                        n = (n or 0) + got_n
                    elif isinstance(got_n, list):
                        n = (n or 0) + len(got_n)
                recovered = n
            except _Unread:
                ambiguous = recovered = None
        out = ui_transfer.section(uid=uid, title=m["title"], entry=entry, facts=facts,
                                  last_filed=last_filed, ambiguous=ambiguous,
                                  recovered=recovered, checking_in=m["checking_in"])
        out["read_at"] = _iso()
        return out

    @app.route("/api/ui/transfer/<uid>")
    def api_ui_transfer(uid):
        """The instrument's Data transfer rows. The store and memory only."""
        try:
            out = section_for(uid)
        except _Unread as exc:
            return unread("this bench's transfer state", exc)
        if out is None:
            return jsonify({"error": "LEM has no instrument %s." % uid}), 404
        resp = jsonify(out)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app.jinja_env.globals["transfer_section"] = section_for

    def first_paint(uid: str) -> Optional[dict]:
        """What a page that includes _transfer_section.html draws before its
        script runs. Never raises: a page carrying this section must not
        become a 500 because the store did not answer.

        * not split (store and LabCore are one gateway): no read at all, as
          `register` promises; the frame says it is reading and the browser
          asks /api/ui/transfer/<uid>, the one read a person pays for by
          opening the page;
        * the store failed: the frame with that sentence, never empty rows;
        * the store has no such instrument: None (the page decides).
        """
        if not split:
            return {"uid": uid, "rows": [], "pending": True}
        try:
            return section_for(uid)
        except _Unread as exc:
            return {"uid": uid, "rows": [], "error": "%s" % (exc or "the store did not answer")}

    app.jinja_env.globals["transfer_first_paint"] = first_paint
    app.jinja_env.filters["transfer_display"] = ui_transfer.display_rows

    # ── conflicts ────────────────────────────────────────────────────────
    def conflicts_view(machine: str = "") -> dict:
        since = _iso(_utc() - timedelta(days=DECIDED_DAYS))
        where = "(resolved_at IS NULL OR resolved_at >= ?)"
        args: list = [since]
        if machine:
            where += " AND machine_uid = ?"
            args.append(machine)
        got = rows("SELECT * FROM result_conflict WHERE " + where +
                   " ORDER BY opened_at", args)
        # the print each held value came from: its run record's time
        prints: Dict[str, str] = {}
        refs = []
        for r in got:
            ref = str(r.get("bench_seq_ref") or "")
            epoch, _, rest = ref.partition(":")
            seq = rest.split("#", 1)[0]
            if epoch and seq.isdigit():
                refs.append((ref, str(r.get("machine_uid") or ""), epoch, int(seq)))
        for ref, uid, epoch, seq in refs:
            rec = rows("SELECT body FROM bench_record WHERE machine_uid = ? AND "
                       "bench_epoch = ? AND bench_seq = ?", [uid, epoch, seq])
            try:
                of = (json.loads(rec[0]["body"]).get("of") or [None])[0] if rec else None
            except (ValueError, TypeError, AttributeError):
                of = None
            if isinstance(of, str) and ":" in of:
                oe, _, os_ = of.partition(":")
                if os_.isdigit():
                    run = rows("SELECT ts FROM bench_record WHERE machine_uid = ? "
                               "AND bench_epoch = ? AND bench_seq = ?",
                               [uid, oe, int(os_)])
                    if run and run[0].get("ts"):
                        prints[ref] = ui_transfer._hm(run[0]["ts"], _utc()) or ""
        view = ui_transfer.conflict_view(got, titles(), prints)
        rejected = []
        for uid, st in sorted(((watch.facts().get("stats") or {}).items())):
            n = ui_transfer._count((st.get("stats") or {}).get("rejected"))
            if n:
                recs = rows("SELECT body, ts FROM bench_record WHERE machine_uid = ? "
                            "AND kind = 'rejected' ORDER BY bench_seq DESC LIMIT 50",
                            [uid])
                cells = []
                for r in recs:
                    try:
                        body = json.loads(r.get("body") or "{}")
                    except ValueError:
                        continue
                    cell = body.get("cell") or []
                    cells.append({"lab_id": str(cell[0]) if len(cell) > 0 else "",
                                  "test_name": str(cell[1]) if len(cell) > 1 else "",
                                  "value": str(cell[2]) if len(cell) > 2 else "",
                                  "error": str(body.get("error") or "")[:200],
                                  "tries": body.get("tries"),
                                  "at": ui_transfer._hm(r.get("ts"), _utc()) or ""})
                rejected.append({"machine_uid": uid, "title": titles().get(uid, uid),
                                 "count": n, "cells": cells,
                                 "href": href(uid, "transfer")})
        view["rejected"] = rejected
        view["machine"] = machine
        view["read_at"] = _iso()
        view["read_hm"] = datetime.now().strftime("%H:%M")
        return view

    @app.route("/api/results/conflicts")
    def api_conflicts():
        machine = (request.args.get("machine") or "").strip()
        try:
            view = conflicts_view(machine)
        except _Unread as exc:
            return unread("the results waiting for a decision", exc)
        resp = jsonify(view)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/results/conflicts/decide", methods=["POST"])
    def api_conflict_decide():
        """A person's decision on one held result. Recorded once; the bench
        files it. 409 when it was already decided, or when LabCore's value is
        no longer the one the person was shown."""
        if not authed():
            return deny()
        who = current_user() or "someone"
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("ref"), str) \
                or not body["ref"]:
            return jsonify({"error": "Say which result: {ref, choice}."}), 400
        choice = body.get("choice")
        if choice not in ("keep", "send"):
            return jsonify({"error": "The choice is keep (LabCore's value stands) or "
                                     "send (the instrument's value is filed)."}), 400
        ref = body["ref"]
        try:
            got = rows("SELECT * FROM result_conflict WHERE bench_seq_ref = ?", [ref])
        except _Unread as exc:
            return unread("that result", exc)
        if not got:
            return jsonify({"error": "There is no held result %s." % ref}), 404
        row = got[0]
        if row.get("resolved_at"):
            same = row.get("choice") == choice and row.get("resolved_by") == who
            if same:
                return jsonify({"ok": True, "ref": ref, "choice": choice,
                                "already": True})
            return jsonify({"error": "It was already decided: %s by %s at %s." % (
                "keep LabCore's value" if row.get("choice") == "keep"
                else "send the instrument's value", row.get("resolved_by") or "?",
                ui_transfer._hm(row.get("resolved_at"), _utc()) or "?")}), 409
        seen = body.get("seen")
        if seen is not None and str(seen) != str(row.get("theirs") or ""):
            return jsonify({"error": "LabCore's value is %s now, not the %s this page "
                                     "showed. Reload and decide again." % (
                                         row.get("theirs"), seen)}), 409
        now = _iso()
        res = store.sql("UPDATE result_conflict SET resolved_at = ?, resolved_by = ?, "
                        "choice = ? WHERE bench_seq_ref = ? AND resolved_at IS NULL",
                        [now, who, choice, ref])
        if not isinstance(res, dict) or res.get("error"):
            return jsonify({"error": "LEM's store did not record the decision (%s); "
                                     "nothing was decided. Try again." % (
                                         (res or {}).get("error") if isinstance(res, dict)
                                         else res)}), 503
        try:
            back = rows("SELECT resolved_by, choice FROM result_conflict WHERE "
                        "bench_seq_ref = ?", [ref])
        except _Unread as exc:
            return unread("the decision back", exc)
        if not back or back[0].get("resolved_by") != who or back[0].get("choice") != choice:
            return jsonify({"error": "Someone else decided it at the same moment; "
                                     "reload to see their decision."}), 409
        watch.invalidate()
        return jsonify({"ok": True, "ref": ref, "choice": choice, "by": who,
                        "at": now})

    @app.route("/results/conflicts")
    def page_conflicts():
        machine = (request.args.get("machine") or "").strip()
        try:
            view, error = conflicts_view(machine), None
        except _Unread as exc:
            view, error = None, str(exc)
        return render_template("conflicts.html", nav="", view=view, error=error,
                               machine=machine,
                               machine_title=titles().get(machine, machine) if machine else "")

    # ── Settings › Transfer ──────────────────────────────────────────────
    def fleet() -> List[dict]:
        """Every instrument LEM holds, not retired: its configuration rows
        and what the instrument record lists (a bench registers a status
        before anyone saves a configuration for it)."""
        configs = rows("SELECT machine_uid, title, retired_at FROM lem_machine_config "
                       "ORDER BY machine_uid")
        retired = {str(r["machine_uid"]) for r in configs if r.get("retired_at")}
        active_by: Dict[str, str] = {}
        for m in machines() or []:
            if m["machine_uid"] not in retired:
                active_by[m["machine_uid"]] = m.get("title") or m["machine_uid"]
        for r in configs:
            uid = str(r["machine_uid"])
            if uid not in retired:
                active_by.setdefault(uid, r.get("title") or uid)
        return [{"machine_uid": u, "title": t} for u, t in active_by.items()]

    def overview() -> dict:
        now = _utc()
        reg_now = time.time()
        facts = watch.facts()
        active = fleet()
        tokens = {str(r["machine_uid"]): r for r in rows(
            "SELECT machine_uid, token_sha256 IS NOT NULL AS enrolled, issued_at, "
            "issued_by, revoked_at, pending_reenrol_at FROM bench_token")}
        first = {str(r["machine_uid"]): r.get("f") for r in rows(
            "SELECT machine_uid, MIN(first_seen) AS f FROM bench_cursor WHERE "
            "mode = 'v2' GROUP BY machine_uid")}
        live = {m["machine_uid"]: m for m in machines() or []}
        benches = []
        for m in active:
            uid = m["machine_uid"]
            entry = registry.get(uid)
            st = (facts.get("stats") or {}).get(uid) or {}
            tok = tokens.get(uid) or {}
            age = ui_transfer._bench_age(entry, reg_now)
            v2 = entry is not None or bool(first.get(uid))
            # enrolled (or asking to) over v2 but no sync yet: module 4,
            # not the older one, and not reporting yet
            enrolling = not v2 and bool(tok)
            ver = (entry or {}).get("module_version") or st.get("module_version")
            unacked = ui_transfer._waiting(entry)
            lm = live.get(uid) or {}
            benches.append({
                "machine_uid": uid, "title": m["title"],
                "mode": "v2" if v2 else "enrolling" if enrolling else "legacy",
                "module": ("v" + str(ver).lstrip("v")) if ver else
                          "module 4, not synced yet" if enrolling else ui_transfer.NOT_REPORTED,
                "road": ui_transfer.ROAD_WORDS.get(str((entry or {}).get("road") or st.get("road") or ""),
                                                   "LabCore (older module)" if not (v2 or enrolling)
                                                   else "—"),
                "delivered_s": round(age, 1) if age is not None else None,
                "waiting": unacked if v2 else None,
                "checking_in": bool(lm.get("live") or lm.get("module_running")),
                "enrolment": ("pending" if tok.get("pending_reenrol_at") else
                              "enrolled" if tok.get("enrolled") else
                              "revoked" if tok.get("revoked_at") else "none"),
                "pending_since": tok.get("pending_reenrol_at"),
                "first_v2": first.get(uid),
                "href": href(uid, "transfer")})
        benches.sort(key=lambda b: (b["mode"] != "legacy", str(b["title"]).lower()))
        # a bench asking to enrol that LEM does not know is listed too
        for uid, tok in sorted(tokens.items()):
            if tok.get("pending_reenrol_at") and uid not in {b["machine_uid"] for b in benches}:
                benches.append({"machine_uid": uid, "title": uid, "mode": "unknown",
                                "module": ui_transfer.NOT_REPORTED, "road": "—",
                                "delivered_s": None, "waiting": None,
                                "checking_in": False, "enrolment": "pending",
                                "pending_since": tok.get("pending_reenrol_at"),
                                "first_v2": None, "href": None})
        fleet_why = overview_bench_reasons(now)
        cust = app.config.get("CUSTODY")
        bridge = app.config.get("BRIDGE")
        cview = cust.view() if cust is not None else None
        refusals = list((cview or {}).get("refusals") or []) if cview else []
        refusals = refusals + [r for r in fleet_why if r not in refusals]
        bstat = bridge.status() if bridge is not None else None
        on = cview["bridge_on"] if cview else None
        import legacy_import
        imp = legacy_import.cached_status(store)
        return {"benches": benches,
                "bridge": {"on": on, "refusals": refusals,
                           "can_change": cview is not None,
                           "outbox": (bstat or {}).get("outbox"),
                           "why_off": (bstat or {}).get("why_off"),
                           "legacy_benches": (bstat or {}).get("legacy_benches")},
                "import": imp, "facts_error": facts.get("error"),
                "read_at": _iso()}

    app.config["TRANSFER_OVERVIEW"] = overview

    @app.route("/api/transfer/overview")
    def api_transfer_overview():
        try:
            out = overview()
        except _Unread as exc:
            return unread("the benches and the bridge", exc)
        resp = jsonify(out)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def fleet_reasons(now: Optional[datetime] = None) -> List[str]:
        """The bench half of bridge-off's refusal, for custody's switch.
        With no split there is no bridge to turn off, and nothing is read."""
        if not split:
            return []
        try:
            return overview_bench_reasons(now)
        except _Unread as exc:
            return ["Which benches run the older module could not be read (%s), so "
                    "whether any still writes to LabCore is unknown." % exc]

    def overview_bench_reasons(now: Optional[datetime] = None) -> List[str]:
        active = fleet()
        first = {str(r["machine_uid"]): r.get("f") for r in rows(
            "SELECT machine_uid, MIN(first_seen) AS f FROM bench_cursor WHERE "
            "mode = 'v2' GROUP BY machine_uid")}
        newest = rows(
            # raw-log: "the bridge has pulled no reading from LabCore for 7
            # days" (§12.1 step 5) is about every row it pulled, hidden or not
            "SELECT MAX(received_at) AS at FROM lem_machine_log WHERE "
            "origin = 'legacy_labcore'")
        return ui_transfer.fleet_refusals(
            machines=active, first_v2=first, newest_legacy_row=(newest[0].get("at") if newest else None),
            now=now or _utc())

    app.config["BRIDGE_FLEET_REFUSALS"] = fleet_reasons
    cust = app.config.get("CUSTODY")
    if cust is not None:
        cust.extra_refusals = fleet_reasons

    @app.route("/api/transfer/benches/<uid>/reset", methods=["POST"])
    def api_bench_reset(uid):
        """Revoke a bench's token. Its next sync is refused (401) and its next
        enrolment waits for a person: a token that may have leaked stops
        speaking for the record now."""
        if not authed():
            return deny()
        who = current_user() or "someone"
        try:
            got = rows("SELECT token_sha256 FROM bench_token WHERE machine_uid = ?", [uid])
        except _Unread as exc:
            return unread("this bench's enrolment", exc)
        if not got or not got[0].get("token_sha256"):
            return jsonify({"error": "%s holds no token to reset." % uid}), 409
        now = _iso()
        try:
            with store.transaction():
                for sql, args in (
                        ("UPDATE bench_token SET token_sha256 = NULL, revoked_at = ?, "
                         "issued_by = ?, enroll_key_sha256 = NULL WHERE machine_uid = ?",
                         [now, "revoked:" + who, uid]),
                        ("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                         "test_name, value, detail) VALUES (?, ?, 'config', '', '', '', ?)",
                         [uid, datetime.now().isoformat(timespec="seconds"),
                          json.dumps({"action": "bench enrolment reset", "by": who})])):
                    res = store.sql(sql, args)
                    if not isinstance(res, dict) or res.get("error"):
                        raise _Unread((res or {}).get("error") if isinstance(res, dict)
                                      else res)
        except _Unread as exc:
            return jsonify({"error": "LEM's store did not record the reset (%s); the "
                                     "bench's token still works." % exc}), 503
        watch.invalidate()
        return jsonify({"ok": True, "machine_uid": uid, "by": who})

    # ── road C: a copied journal folder ──────────────────────────────────
    @app.route("/api/transfer/journal-import", methods=["POST"])
    def api_journal_import():
        """The bench's journal segments, copied by hand (§6.2 road C). Each
        line's CRC is checked; per (uid, epoch) only records contiguous from
        what LEM holds are taken, through the sync's own ingest, so a folder
        imported twice adds nothing. `?dry=1` says what it would do."""
        if not authed():
            return deny()
        dry = request.args.get("dry") in ("1", "true")
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "Choose the bench's journal folder (its "
                                     "seg-*.jsonl files)."}), 400
        import bench_api
        groups: Dict[tuple, Dict[int, tuple]] = {}
        damaged: Dict[tuple, int] = {}
        unreadable = 0
        total = 0
        for f in files:
            data = f.read(MAX_IMPORT_BYTES + 1)
            total += len(data)
            if total > MAX_IMPORT_BYTES:
                return jsonify({"error": "More than 64 MB at once; import the "
                                         "folder a few segments at a time."}), 413
            for line in data.splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    unreadable += 1
                    continue
                if not isinstance(rec, dict):
                    unreadable += 1
                    continue
                key = (str(rec.get("uid") or ""), str(rec.get("epoch") or ""))
                crc = rec.get("crc")
                body = {k: v for k, v in rec.items() if k != "crc"}
                raw = bench_api.canonical(body)
                seq = rec.get("seq")
                if (not isinstance(crc, str) or crc != bench_api.record_crc(raw)
                        or isinstance(seq, bool) or not isinstance(seq, int)
                        or not key[0] or not key[1] or not body.get("kind")):
                    damaged[key] = damaged.get(key, 0) + 1
                    continue
                groups.setdefault(key, {})[seq] = (body, raw)
        out = []
        for key in sorted(set(groups) | set(damaged)):
            uid, epoch = key
            recs = groups.get(key, {})
            entry = {"machine_uid": uid, "epoch": epoch, "records": len(recs),
                     "damaged": damaged.get(key, 0), "new": 0, "held": 0,
                     "imported": 0, "note": ""}
            out.append(entry)
            try:
                m = known(uid) if uid else None
            except _Unread as exc:
                entry["note"] = "could not check this uid: %s" % exc
                continue
            if m is None:
                entry["note"] = "LEM has no instrument %s; nothing imported" % (uid or "?")
                continue
            entry["title"] = m["title"]
            try:
                cur = rows("SELECT acked_seq, digest FROM bench_cursor WHERE "
                           "machine_uid = ? AND bench_epoch = ?", [uid, epoch])
            except _Unread as exc:
                entry["note"] = "could not read what LEM holds: %s" % exc
                continue
            acked = int(cur[0]["acked_seq"]) if cur else 0
            entry["held"] = sum(1 for s in recs if s <= acked)
            run = []
            s = acked + 1
            while s in recs:
                run.append(s)
                s += 1
            entry["new"] = len(run)
            after = sorted(x for x in recs if x > s)
            if after:
                entry["note"] = ("a gap: seq %d is not in this folder, so %d later "
                                 "%s stay at the bench until it arrives" % (
                                     s, len(after), "record" if len(after) == 1
                                     else "records"))
            if dry or not run:
                continue
            now = _iso()
            try:
                with store.transaction():
                    ing = bench_api.Ingest(store, uid, epoch, now)
                    cur = (ing.q("SELECT acked_seq, digest FROM bench_cursor WHERE "
                                 "machine_uid = ? AND bench_epoch = ?",
                                 [uid, epoch]) or [None])[0]
                    acked2 = int(cur["acked_seq"]) if cur else 0
                    digest = (cur.get("digest") if cur else None) or bench_api.DIGEST_ZERO
                    took = 0
                    seq = acked2 + 1
                    while seq in recs:
                        body, raw = recs[seq]
                        ing.record(seq, body, raw)
                        digest = bench_api.chain(digest, raw)
                        took += 1
                        seq += 1
                    if took:
                        ing.x("INSERT INTO bench_cursor (machine_uid, bench_epoch, "
                              "acked_seq, digest, first_seen, last_seen, road, mode) "
                              "VALUES (?, ?, ?, ?, ?, ?, 'folder', 'v2') ON CONFLICT("
                              "machine_uid, bench_epoch) DO UPDATE SET acked_seq = "
                              "excluded.acked_seq, digest = excluded.digest",
                              [uid, epoch, seq - 1, digest, now, now])
                        ing.x("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                              "lab_id, test_name, value, detail) VALUES (?, ?, "
                              "'config', '', '', '', ?)",
                              [uid, datetime.now().isoformat(timespec="seconds"),
                               json.dumps({"action": "journal folder imported",
                                           "epoch": epoch, "from_seq": acked2 + 1,
                                           "to_seq": seq - 1,
                                           "by": current_user() or "someone"})])
                entry["imported"] = took
            except Exception as exc:                    # noqa: BLE001
                entry["note"] = ("nothing imported: LEM's store refused (%s)" % exc)[:240]
        watch.invalidate()
        status = 200
        if not out:
            return jsonify({"error": "No journal records in those files (%d lines "
                                     "could not be read)." % unreadable}), 400
        return jsonify({"dry": dry, "benches": out, "unreadable": unreadable}), status

    # ── dedupe approvals, listed ─────────────────────────────────────────
    @app.route("/api/dedupe/approvals")
    def api_dedupe_approvals():
        """Every recorded dedupe decision (D7), and whether it was applied, so
        an approval made yesterday can still be applied today."""
        if not authed():
            return deny()
        try:
            got = rows("SELECT a.id, a.machine_uid, a.rule, a.run_id, a.candidates, "
                       "a.approved_by, a.approved_at, a.decision, (SELECT COUNT(*) "
                       "FROM log_annotation l WHERE l.approval_id = a.id) AS applied "
                       "FROM annotation_approval a ORDER BY a.id DESC LIMIT 200")
        except _Unread as exc:
            return unread("the dedupe approvals", exc)
        t = titles()
        return jsonify({"approvals": [dict(r, title=t.get(str(r.get("machine_uid")),
                                                           r.get("machine_uid")))
                                      for r in got]})

