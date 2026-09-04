// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title FaceProofRegistry
/// @notice Tamper-evident anchor for face-match evidence records.
/// @dev Only the keccak256 digest of the canonical evidence JSON is stored on
///      chain. No image, embedding or personal data is written to the ledger --
///      the chain proves *when* a record existed and that it has not changed
///      since, without itself becoming a biometric database.
contract FaceProofRegistry {
    struct Anchor {
        address submitter;
        uint64 timestamp;
        uint64 blockNumber;
        string uri; // optional off-chain pointer (IPFS CID, URL); may be empty
    }

    mapping(bytes32 => Anchor) private _anchors;
    bytes32[] private _hashes;

    event Anchored(
        bytes32 indexed recordHash,
        address indexed submitter,
        uint64 timestamp,
        uint64 blockNumber,
        string uri
    );

    error AlreadyAnchored(bytes32 recordHash, uint64 timestamp);
    error EmptyHash();

    /// @notice Anchor an evidence record digest. Reverts if already present --
    ///         the first submission wins, so re-anchoring cannot rewrite history.
    function anchor(bytes32 recordHash, string calldata uri) external returns (uint256 index) {
        if (recordHash == bytes32(0)) revert EmptyHash();
        Anchor storage existing = _anchors[recordHash];
        if (existing.timestamp != 0) revert AlreadyAnchored(recordHash, existing.timestamp);

        _anchors[recordHash] = Anchor({
            submitter: msg.sender,
            timestamp: uint64(block.timestamp),
            blockNumber: uint64(block.number),
            uri: uri
        });
        _hashes.push(recordHash);

        emit Anchored(recordHash, msg.sender, uint64(block.timestamp), uint64(block.number), uri);
        return _hashes.length - 1;
    }

    /// @notice Look up an anchored digest.
    /// @return exists      true if this exact digest was anchored
    /// @return submitter   address that anchored it
    /// @return timestamp   block timestamp of the anchoring transaction
    /// @return blockNumber block the anchor landed in
    /// @return uri         optional off-chain pointer stored alongside
    function verify(bytes32 recordHash)
        external
        view
        returns (bool exists, address submitter, uint64 timestamp, uint64 blockNumber, string memory uri)
    {
        Anchor storage a = _anchors[recordHash];
        return (a.timestamp != 0, a.submitter, a.timestamp, a.blockNumber, a.uri);
    }

    function count() external view returns (uint256) {
        return _hashes.length;
    }

    function hashAt(uint256 index) external view returns (bytes32) {
        return _hashes[index];
    }
}
