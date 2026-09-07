"""One command family behind the stable guided CLI facade."""

from __future__ import annotations

def _candidate_store(facade: object, arguments: argparse.Namespace) -> Path:
    return arguments.work_dir.expanduser().resolve() / "candidates"

def _candidate_create(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _candidate_store = getattr(facade, '_candidate_store')
    _document = getattr(facade, '_document')
    create_candidate = getattr(facade, 'create_candidate')
    result = create_candidate(
        store_root=_candidate_store(arguments),
        specification=arguments.spec,
    )
    return _document("candidate create", ok=True, phase="candidate-created", result=result)

def _candidate_record(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _candidate_store = getattr(facade, '_candidate_store')
    _document = getattr(facade, '_document')
    record_candidate_run = getattr(facade, 'record_candidate_run')
    result = record_candidate_run(
        store_root=_candidate_store(arguments),
        candidate_id=arguments.candidate_id,
        run_path=arguments.run,
    )
    return _document(
        "candidate record", ok=True, phase="candidate-run-recorded", result=result
    )

def _candidate_status(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _candidate_store = getattr(facade, '_candidate_store')
    _document = getattr(facade, '_document')
    candidate_status = getattr(facade, 'candidate_status')
    result = candidate_status(
        store_root=_candidate_store(arguments),
        candidate_id=arguments.candidate_id,
    )
    return _document("candidate status", ok=True, phase="candidate-status", result=result)

def _candidate_decide(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _candidate_store = getattr(facade, '_candidate_store')
    _document = getattr(facade, '_document')
    decide_candidate = getattr(facade, 'decide_candidate')
    result = decide_candidate(
        store_root=_candidate_store(arguments),
        candidate_id=arguments.candidate_id,
        decision=arguments.decision,
        reason=arguments.reason,
    )
    return _document("candidate decide", ok=True, phase="candidate-decided", result=result)

def _diagnose_runtime(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    RuntimeDiagnosticsError = getattr(facade, 'RuntimeDiagnosticsError')
    UserInstallerError = getattr(facade, 'UserInstallerError')
    _document = getattr(facade, '_document')
    _regular = getattr(facade, '_regular')
    collect_runtime_snapshot = getattr(facade, 'collect_runtime_snapshot')
    json = getattr(facade, 'json')
    store_runtime_snapshot = getattr(facade, 'store_runtime_snapshot')
    validate_runtime_snapshot = getattr(facade, 'validate_runtime_snapshot')
    if arguments.snapshot_file is not None:
        try:
            snapshot = json.loads(
                _regular(arguments.snapshot_file, "runtime snapshot").read_text(
                    encoding="utf-8"
                )
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeDiagnosticsError("runtime snapshot is invalid JSON") from exc
        if not isinstance(snapshot, dict):
            raise RuntimeDiagnosticsError("runtime snapshot is invalid")
        validate_runtime_snapshot(snapshot)
    else:
        if arguments.session_dir is None:
            raise UserInstallerError("live runtime diagnosis requires --session-dir")
        snapshot = collect_runtime_snapshot(
            session_dir=arguments.session_dir,
            station_ipv4=arguments.station_ipv4,
        )
    result = store_runtime_snapshot(
        store_root=arguments.work_dir.expanduser().resolve() / "diagnostics",
        snapshot=snapshot,
        fault_class=arguments.fault_class,
        candidate_id=arguments.candidate_id,
    )
    return _document(
        "diagnose-runtime",
        ok=True,
        phase="runtime-evidence-captured",
        result=result,
    )

def _hypothesis_record(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    record_hypothesis = getattr(facade, 'record_hypothesis')
    result = record_hypothesis(
        ledger_path=arguments.work_dir.expanduser().resolve()
        / "diagnostics/hypotheses.json",
        snapshot_sha256=arguments.snapshot_sha256,
        hypothesis_id=arguments.hypothesis_id,
        change_identity=arguments.change_identity,
        result=arguments.result,
        evidence=arguments.evidence,
    )
    return _document(
        "hypothesis-record",
        ok=True,
        phase="hypothesis-recorded",
        result=result,
    )
