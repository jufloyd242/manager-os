"""Logic for balancing staffing capacity across team members."""

from __future__ import annotations


def balance_staffing_capacity(
    allocations: dict[str, float],
    standard_capacity: float = 100.0,
    overallocated_threshold: float = 100.0,
    underallocated_threshold: float = 80.0,
    max_receiver_capacity: float = 80.0,
) -> dict:
    """Balances staffing capacity by shifting excess allocation from overallocated
    members to underallocated members.
    
    Args:
        allocations: Map of team member names to current allocation percentages.
        standard_capacity: Standard/target capacity for a team member (default 100%).
        overallocated_threshold: Threshold above which a member is considered overallocated.
        underallocated_threshold: Threshold below which a member is considered underallocated.
        max_receiver_capacity: Maximum capacity an underallocated member can receive up to.
        
    Returns:
        A dict containing original and balanced allocations, detailed transfer list, and statuses.
    """
    original_allocations = {name: float(val) for name, val in allocations.items()}
    balanced = {name: float(val) for name, val in allocations.items()}
    
    # Identify overallocated and underallocated
    overallocated_before = sorted([
        name for name, alloc in original_allocations.items()
        if alloc > overallocated_threshold
    ])
    underallocated_before = sorted([
        name for name, alloc in original_allocations.items()
        if alloc < underallocated_threshold
    ])
    
    transfers = []
    
    # Distribute excess capacity deterministically (sorted alphabetically by name)
    for provider in overallocated_before:
        provider_excess = balanced[provider] - standard_capacity
        if provider_excess <= 1e-9:
            continue
            
        for receiver in underallocated_before:
            if provider_excess <= 1e-9:
                break
                
            receiver_room = max_receiver_capacity - balanced[receiver]
            if receiver_room <= 1e-9:
                continue
                
            transfer_amount = min(provider_excess, receiver_room)
            if transfer_amount > 1e-9:
                balanced[provider] -= transfer_amount
                balanced[receiver] += transfer_amount
                provider_excess -= transfer_amount
                transfers.append({
                    "from": provider,
                    "to": receiver,
                    "amount": round(transfer_amount, 2),
                })
                
    # Re-evaluate post-balancing states
    overallocated_after = sorted([
        name for name, alloc in balanced.items()
        if alloc > overallocated_threshold
    ])
    underallocated_after = sorted([
        name for name, alloc in balanced.items()
        if alloc < underallocated_threshold
    ])
    
    status = "no_change_needed"
    if overallocated_before:
        if not overallocated_after:
            status = "fully_balanced"
        else:
            status = "partially_balanced"
            
    return {
        "original_allocations": {k: round(v, 2) for k, v in original_allocations.items()},
        "balanced_allocations": {k: round(v, 2) for k, v in balanced.items()},
        "transfers": transfers,
        "overallocated_before": overallocated_before,
        "underallocated_before": underallocated_before,
        "overallocated_after": overallocated_after,
        "underallocated_after": underallocated_after,
        "status": status,
    }
