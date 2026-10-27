"""Exceptions raised by the LOST Python API.

Everything derives from :class:`LostError`, so ``except LostError`` catches any failure that
originates in LOST or in this wrapper.
"""

from __future__ import annotations

import re
import signal
from typing import Sequence

# Lines LOST prints (to stdout or stderr) that explain why it exited.
_DIAGNOSTIC_PATTERNS = (
    re.compile(r"^ERROR:.*", re.MULTILINE),
    re.compile(r"^Illegal .*", re.MULTILINE),
    re.compile(r"^Error .*", re.MULTILINE),
    re.compile(r"^.*unrecognized option.*", re.MULTILINE),
    re.compile(r"^.*requires an argument.*", re.MULTILINE),
    re.compile(r"^.*Assertion .* failed.*", re.MULTILINE),
    re.compile(r"^.*AddressSanitizer.*", re.MULTILINE),
    re.compile(r"^Can't convert .*", re.MULTILINE),
    re.compile(r"^PNG Read status: (?!success).*", re.MULTILINE | re.IGNORECASE),
)
_UNRECOGNIZED_OPTION = re.compile(r"unrecognized option '--([^'=]+)")


class LostError(Exception):
    """Base class for every error raised by this package."""


class LostConfigError(LostError, ValueError):
    """A configuration was rejected before LOST was run."""


class LostBinaryNotFoundError(LostError, FileNotFoundError):
    """The LOST executable or the star catalog could not be located."""


class LostBuildError(LostError):
    """Building LOST with ``make`` failed."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


class LostExecutionError(LostError):
    """LOST exited unsuccessfully.

    Attributes hold the full command and captured output so failures can be reproduced on the
    command line.
    """

    def __init__(
        self,
        command: Sequence[str],
        returncode: int | None,
        stdout: str,
        stderr: str,
        message: str | None = None,
    ) -> None:
        self.command = list(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.diagnostics = extract_diagnostics(stdout, stderr)
        super().__init__(message or self._default_message())

    @property
    def crashed(self) -> bool:
        """True when LOST was killed by a signal (usually a failed ``assert``) rather than exiting."""
        return self.returncode is not None and (self.returncode < 0 or self.returncode in (134, 139))

    def _default_message(self) -> str:
        if self.returncode is not None and self.returncode < 0:
            try:
                status = f"was killed by {signal.Signals(-self.returncode).name}"
            except ValueError:
                status = f"was killed by signal {-self.returncode}"
        else:
            status = f"exited with status {self.returncode}"
        message = f"LOST {status}"
        if self.diagnostics:
            message += ": " + "; ".join(self.diagnostics)
        elif self.crashed:
            message += " (most likely a failed internal assertion; check the configuration values)"
        return message


class LostUnsupportedOptionError(LostExecutionError):
    """The LOST executable does not know an option the wrapper passed.

    This usually means the binary was built from a different version or fork of LOST than the
    configuration expects (for example ``interesting_threshold`` needs a binary that has
    ``--generate-interesting-threshold``).
    """

    def __init__(self, option: str, *args, **kwargs) -> None:
        self.option = option
        super().__init__(*args, **kwargs)

    def _default_message(self) -> str:
        return (
            f"This LOST binary does not support --{self.option}. Rebuild LOST from a version that "
            f"has it, or remove the setting that emits it."
        )


class LostTimeoutError(LostExecutionError):
    """LOST did not finish within the configured timeout."""


def extract_diagnostics(stdout: str, stderr: str) -> list[str]:
    """Return LOST's explanatory error lines from its output, in order, without duplicates."""
    found: list[str] = []
    for text in (stderr, stdout):
        for pattern in _DIAGNOSTIC_PATTERNS:
            for match in pattern.finditer(text or ""):
                line = match.group(0).strip()
                if line and line not in found:
                    found.append(line)
    return found


def execution_error(
    command: Sequence[str], returncode: int | None, stdout: str, stderr: str
) -> LostExecutionError:
    """Build the most specific :class:`LostExecutionError` for a failed run."""
    match = _UNRECOGNIZED_OPTION.search(stderr or "")
    if match:
        return LostUnsupportedOptionError(match.group(1), command, returncode, stdout, stderr)
    return LostExecutionError(command, returncode, stdout, stderr)
