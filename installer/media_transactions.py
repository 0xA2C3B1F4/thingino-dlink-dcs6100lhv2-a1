"""Transaction entrypoints grouped by operation, with explicit dependencies.

Public call signatures remain in installer.media. No transaction looks up a
caller module.
"""

from .media_io import (
    _write_verified_temporary,
    _sync_directory,
)
from .media_install_set import (
    stage_verified_install_set,
    activate_staged_install_set,
    deactivate_staged_install_set,
)
from .media_replacement import (
    replace_passive_bootstrap,
)
from .media_capture import (
    stage_passive_verified_package,
    activate_passive_verified_package,
)
from .media_packages import (
    stage_verified_package,
    deactivate_verified_package,
)

__all__ = [
    '_write_verified_temporary',
    '_sync_directory',
    'stage_verified_install_set',
    'activate_staged_install_set',
    'deactivate_staged_install_set',
    'replace_passive_bootstrap',
    'stage_passive_verified_package',
    'activate_passive_verified_package',
    'stage_verified_package',
    'deactivate_verified_package',
]
