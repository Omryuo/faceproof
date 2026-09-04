"""Deploy to / read from an EVM chain.

Three backends, same interface:

  tester  -- in-process py-evm chain (eth-tester). Zero setup, zero cost, but
             state lives only for the life of the process.
  local   -- JSON-RPC at http://127.0.0.1:8545 (ganache/anvil). Persists across
             CLI invocations, which is what makes a separate re-verify run a
             real re-verify.
  <named> -- a public testnet (Sepolia, Polygon Amoy, ...) via RPC_URL +
             PRIVATE_KEY.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3

from .compile import compile_contract

ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY_DIR = ROOT / "out"

NETWORKS = {
    "tester": {"kind": "tester", "explorer": None},
    "local": {"kind": "rpc", "rpc": "http://127.0.0.1:8545", "explorer": None},
    "sepolia": {
        "kind": "rpc",
        "rpc": None,  # from RPC_URL
        "chain_id": 11155111,
        "explorer": "https://sepolia.etherscan.io/tx/",
        "poa": False,
    },
    "amoy": {
        "kind": "rpc",
        "rpc": None,
        "chain_id": 80002,
        "explorer": "https://amoy.polygonscan.com/tx/",
        "poa": True,
    },
}


class ChainError(RuntimeError):
    pass


class AlreadyAnchored(ChainError):
    """The digest is already on chain. Not a failure -- it *is* the proof."""

    def __init__(self, record_hash: str, existing: dict):
        self.record_hash = record_hash
        self.existing = existing
        super().__init__(
            f"{record_hash} was already anchored in block "
            f"{existing['block_number']} by {existing['submitter']}"
        )


@dataclass
class AnchorReceipt:
    record_hash: str
    tx_hash: str
    block_number: int
    block_timestamp: int
    contract_address: str
    chain_id: int
    network: str
    submitter: str
    gas_used: int
    explorer_url: str | None = None

    def to_dict(self) -> dict:
        d = {
            "network": self.network,
            "chain_id": self.chain_id,
            "contract_address": self.contract_address,
            "tx_hash": self.tx_hash,
            "block_number": self.block_number,
            "block_timestamp": self.block_timestamp,
            "submitter": self.submitter,
            "gas_used": self.gas_used,
            "record_hash": self.record_hash,
        }
        if self.explorer_url:
            d["explorer_url"] = self.explorer_url
        return d


class ChainClient:
    def __init__(self, network: str = "tester", rpc_url: str | None = None):
        if network not in NETWORKS and not rpc_url:
            raise ChainError(
                f"unknown network {network!r}; known: {sorted(NETWORKS)} "
                "(or pass an explicit --rpc-url)"
            )
        self.network = network
        self.cfg = NETWORKS.get(network, {"kind": "rpc", "explorer": None})
        self.artifact = compile_contract()
        self.w3 = self._connect(rpc_url)
        self.account = self._setup_account()
        self.contract = None

    # -- connection ------------------------------------------------------

    def _connect(self, rpc_url: str | None) -> Web3:
        if self.cfg.get("kind") == "tester":
            from web3 import EthereumTesterProvider

            return Web3(EthereumTesterProvider())

        url = rpc_url or os.getenv("RPC_URL") or self.cfg.get("rpc")
        if not url:
            raise ChainError(
                f"network {self.network!r} needs an RPC endpoint: set RPC_URL or pass --rpc-url"
            )
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
        if not w3.is_connected():
            raise ChainError(f"could not connect to {url}")
        if self.cfg.get("poa"):
            from web3.middleware import ExtraDataToPOAMiddleware

            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        return w3

    def _setup_account(self) -> str:
        pk = os.getenv("PRIVATE_KEY")
        if pk:
            from web3.middleware import SignAndSendRawMiddlewareBuilder

            acct = self.w3.eth.account.from_key(
                pk if pk.startswith("0x") else "0x" + pk
            )
            self.w3.middleware_onion.inject(
                SignAndSendRawMiddlewareBuilder.build(acct), layer=0
            )
            self.w3.eth.default_account = acct.address
            return acct.address
        accounts = self.w3.eth.accounts
        if not accounts:
            raise ChainError(
                "no unlocked account and PRIVATE_KEY is unset -- cannot send transactions"
            )
        self.w3.eth.default_account = accounts[0]
        return accounts[0]

    # -- deployment ------------------------------------------------------

    @property
    def _deployment_file(self) -> Path:
        return DEPLOY_DIR / f"deployment.{self.network}.json"

    def deploy(self) -> str:
        Contract = self.w3.eth.contract(
            abi=self.artifact["abi"], bytecode=self.artifact["bytecode"]
        )
        tx = Contract.constructor().transact({"from": self.account})
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx, timeout=300)
        address = rcpt.contractAddress
        self.contract = self.w3.eth.contract(address=address, abi=self.artifact["abi"])
        DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
        self._deployment_file.write_text(
            json.dumps(
                {
                    "network": self.network,
                    "chain_id": self.w3.eth.chain_id,
                    "address": address,
                    "deployer": self.account,
                    "tx_hash": rcpt.transactionHash.hex(),
                    "block_number": rcpt.blockNumber,
                    "solc_version": self.artifact.get("solcVersion"),
                },
                indent=2,
            )
            + "\n"
        )
        return address

    def load(self, address: str | None = None) -> str:
        """Attach to a deployed registry: explicit address > env > saved file."""
        address = address or os.getenv("CONTRACT_ADDRESS")
        if not address and self._deployment_file.exists():
            address = json.loads(self._deployment_file.read_text())["address"]
        if not address:
            raise ChainError(
                f"no deployment found for {self.network!r}; run `deploy` first "
                "or set CONTRACT_ADDRESS"
            )
        address = Web3.to_checksum_address(address)
        if self.w3.eth.get_code(address) in (b"", b"0x"):
            raise ChainError(f"no contract code at {address} on {self.network!r}")
        self.contract = self.w3.eth.contract(address=address, abi=self.artifact["abi"])
        return address

    def ensure(self, address: str | None = None) -> str:
        """Attach if possible, otherwise deploy."""
        try:
            return self.load(address)
        except ChainError:
            return self.deploy()

    # -- read / write ----------------------------------------------------

    def anchor(self, record_hash: str, uri: str = "") -> AnchorReceipt:
        if self.contract is None:
            raise ChainError("no contract attached; call ensure()/load() first")
        digest = Web3.to_bytes(hexstr=record_hash)
        if len(digest) != 32:
            raise ChainError(f"record hash must be 32 bytes, got {len(digest)}")

        existing = self.lookup(record_hash)
        if existing["exists"]:
            raise AlreadyAnchored(record_hash, existing)

        tx = self.contract.functions.anchor(digest, uri).transact({"from": self.account})
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx, timeout=300)
        if rcpt.status != 1:
            raise ChainError(f"anchor transaction reverted: {rcpt.transactionHash.hex()}")
        block = self.w3.eth.get_block(rcpt.blockNumber)
        tx_hex = rcpt.transactionHash.hex()
        if not tx_hex.startswith("0x"):
            tx_hex = "0x" + tx_hex
        explorer = self.cfg.get("explorer")
        return AnchorReceipt(
            record_hash=record_hash,
            tx_hash=tx_hex,
            block_number=rcpt.blockNumber,
            block_timestamp=block.timestamp,
            contract_address=self.contract.address,
            chain_id=self.w3.eth.chain_id,
            network=self.network,
            submitter=self.account,
            gas_used=rcpt.gasUsed,
            explorer_url=(explorer + tx_hex) if explorer else None,
        )

    def lookup(self, record_hash: str) -> dict:
        if self.contract is None:
            raise ChainError("no contract attached; call ensure()/load() first")
        digest = Web3.to_bytes(hexstr=record_hash)
        exists, submitter, ts, blk, uri = self.contract.functions.verify(digest).call()
        return {
            "exists": bool(exists),
            "submitter": submitter,
            "timestamp": int(ts),
            "block_number": int(blk),
            "uri": uri,
            "contract_address": self.contract.address,
            "chain_id": self.w3.eth.chain_id,
            "network": self.network,
        }

    def count(self) -> int:
        if self.contract is None:
            raise ChainError("no contract attached")
        return int(self.contract.functions.count().call())
