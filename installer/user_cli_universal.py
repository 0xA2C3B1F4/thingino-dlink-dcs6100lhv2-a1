"""CLI presentation for the shared camera installation operations."""

from __future__ import annotations

from dataclasses import fields

from . import camera_setup, install_actions
from .install_results import document
from .user_cli_media import select_media, confirm_plan
from .user_cli_project import event

UNIVERSAL_WRITE_CONFIRMATION = "STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3"
UNIVERSAL_HANDOFF_CONFIRMATION = "MTD1-MTD2-WRITTEN"


def _setup_call(arguments, request_type, operation):
    from .private_config import read_confirmed_private_input
    values = {}
    for field in fields(request_type):
        if field.name == "wifi":
            values["wifi"] = camera_setup.WifiInput(*read_confirmed_private_input(secrets_fd=arguments.secrets_fd))
        else:
            values[field.name] = getattr(arguments, field.name)
    return operation(request_type(**values), emit=lambda item: event(arguments, item.phase))


def _universal_init_session(facade, arguments):
    return _setup_call(arguments, camera_setup.SessionInputs, camera_setup.initialize_session)


def _universal_configure(facade, arguments):
    return _setup_call(arguments, camera_setup.ConfigurationInputs, camera_setup.configure_camera)


def _universal_provision(facade, arguments):
    return _setup_call(arguments, camera_setup.ProvisionInputs, camera_setup.provision_camera)


def _universal_authorize(facade, arguments):
    return _setup_call(arguments, camera_setup.AuthorizationInputs, camera_setup.authorize_camera)


def _universal_verify(facade, arguments):
    return _setup_call(arguments, camera_setup.VerifyInputs, camera_setup.verify_camera)


def _universal_verify_readback(facade, arguments):
    from .post_install_readback import PostInstallReadbackInputs, verify_post_install_readback

    return _setup_call(arguments, PostInstallReadbackInputs, verify_post_install_readback)


def _media_call(arguments, request_type, planner, operation):
    select_media(arguments)
    request = request_type(**{field.name: getattr(arguments, field.name) for field in fields(request_type)})
    planned = planner(request)
    plan = planned[0] if isinstance(planned, tuple) else planned
    if getattr(arguments, "plan_only", False):
        return document(plan.operation, ok=True, phase="write-plan-ready",
                        result={"plan": plan.document(), "plan_sha256": plan.identity,
                                "required_confirmations": plan.required_confirmations(),
                                "write_set": [], "writes_performed": False})
    confirmation = confirm_plan(arguments, plan)
    return operation(request, confirmation, emit=lambda item: event(arguments, item.phase))


def _universal_stage(facade, arguments):
    return _media_call(arguments, install_actions.UniversalStageInputs,
                       install_actions.plan_universal_stage, install_actions.stage_universal)


def _universal_handoff(facade, arguments):
    return _media_call(arguments, install_actions.UniversalStageInputs,
                       install_actions.plan_universal_handoff, install_actions.handoff_universal)


def _universal_evacuate_recovery(facade, arguments):
    return _media_call(arguments, install_actions.EvacuationInputs,
                       install_actions.plan_evacuation, install_actions.evacuate_recovery)


def _universal_quarantine_inconsistent_media(facade, arguments):
    return _media_call(
        arguments,
        install_actions.InconsistentMediaQuarantineInputs,
        install_actions.plan_inconsistent_media_quarantine,
        install_actions.quarantine_inconsistent_media,
    )
