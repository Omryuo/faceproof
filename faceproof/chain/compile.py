"""Compile FaceProofRegistry.sol, with a committed artifact as the fast path.

The build output (ABI + bytecode) is cached in artifacts/ and committed, so a
fresh clone can deploy without installing the Solidity compiler at all.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "contracts" / "FaceProofRegistry.sol"
ARTIFACT = ROOT / "artifacts" / "FaceProofRegistry.json"
SOLC_VERSION = "0.8.24"


def compile_contract(force: bool = False) -> dict:
    """Return {'abi': [...], 'bytecode': '0x...'}."""
    if ARTIFACT.exists() and not force:
        return json.loads(ARTIFACT.read_text())
    return build(write=True)


def build(write: bool = True) -> dict:
    import solcx

    if SOLC_VERSION not in [str(v) for v in solcx.get_installed_solc_versions()]:
        solcx.install_solc(SOLC_VERSION)

    out = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {"FaceProofRegistry.sol": {"content": SOURCE.read_text()}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {
                    "*": {"*": ["abi", "evm.bytecode.object", "metadata"]}
                },
            },
        },
        solc_version=SOLC_VERSION,
    )
    c = out["contracts"]["FaceProofRegistry.sol"]["FaceProofRegistry"]
    artifact = {
        "contractName": "FaceProofRegistry",
        "solcVersion": SOLC_VERSION,
        "abi": c["abi"],
        "bytecode": "0x" + c["evm"]["bytecode"]["object"],
    }
    if write:
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(json.dumps(artifact, indent=2) + "\n")
    return artifact
