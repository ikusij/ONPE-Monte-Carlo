import numpy as np
from typing import Literal
from dataclasses import dataclass, field
 
@dataclass
class MonteCarloConfig:
    n_simulations: int = 10_000
    prior: float | Literal["flat", "jeffreys"] = "flat"
    confidence_level: float = 0.95
    random_seed: int | None = None
 
@dataclass
class CandidateResult:
    name: str
    votes_counted: int
    current_share: float
    projected_share: float
    ci_low: float
    ci_high: float
    win_probability: float
    std: float
 
@dataclass
class SimulationResult:
    candidates: list[CandidateResult]
    projected_winner: CandidateResult
    votes_counted: int
    votes_remaining: int
    total_votes: int
    pct_counted: float
    n_simulations: int
    prior_used: float
    confidence_level: float
    raw_finals: np.ndarray
    candidate_names: list[str]
 
def monte_carlo_simulation(
    data: dict,
    config: MonteCarloConfig | None = None,
) -> SimulationResult:
    if config is None:
        config = MonteCarloConfig()
 
    if config.prior == "flat":
        prior_val = 1.0
    elif config.prior == "jeffreys":
        prior_val = 0.5
    else:
        prior_val = float(config.prior)
 
    if not (0.0 < config.confidence_level < 1.0):
        raise ValueError("confidence_level must be between 0 and 1 (exclusive).")
 
    raw_candidates: dict[str, int] = dict(data["candidatos"])
    votes_counted: int = int(data["votosEmitidos"])
    votes_remaining: int = int(data["votosRestantes"])
    total_votes: int = votes_counted + votes_remaining

    if votes_counted == 0:
        print(f"[skip] {data['ubigeo_distrito']}: no votes counted.")
        return None
 
    names = list(raw_candidates.keys())
    counts = np.array([raw_candidates[n] for n in names], dtype=float)
 
    count_sum = counts.sum()
    if abs(count_sum - votes_counted) > votes_counted * 0.05:
        print(
            f"[skip] {data['ubigeo_distrito']}: candidate sum ({count_sum:.0f}) "
            f"differs from votosEmitidos ({votes_counted}) by more than 5%."
        )
        return None
 
    alphas = counts + prior_val
 
    if np.any(alphas <= 0):
        zero_cands = [names[i] for i, a in enumerate(alphas) if a <= 0]
        raise ValueError(f"α ≤ 0 for candidates {zero_cands}. Use prior > 0.")
 
    counted_frac = votes_counted / total_votes
    remaining_frac = votes_remaining / total_votes
    current_shares = counts / count_sum
 
    rng = np.random.default_rng(config.random_seed)
    remaining_draws = rng.dirichlet(alphas, size=config.n_simulations)
 
    finals = (
        current_shares[np.newaxis, :] * counted_frac
        + remaining_draws * remaining_frac
    )
 
    lo = (1.0 - config.confidence_level) / 2.0
    hi = 1.0 - lo
 
    means = finals.mean(axis=0)
    stds  = finals.std(axis=0)
    ci_lo = np.quantile(finals, lo, axis=0)
    ci_hi = np.quantile(finals, hi, axis=0)
 
    winner_per_sim = np.argmax(finals, axis=1)
    win_counts = np.bincount(winner_per_sim, minlength=len(names))
    win_probs = win_counts / config.n_simulations
 
    candidate_results = [
        CandidateResult(
            name=names[i],
            votes_counted=int(counts[i]),
            current_share=float(current_shares[i]),
            projected_share=float(means[i]),
            ci_low=float(ci_lo[i]),
            ci_high=float(ci_hi[i]),
            win_probability=float(win_probs[i]),
            std=float(stds[i]),
        )
        for i in range(len(names))
    ]
 
    candidate_results.sort(key=lambda c: c.projected_share, reverse=True)
 
    return SimulationResult(
        candidates=candidate_results,
        projected_winner=candidate_results[0],
        votes_counted=votes_counted,
        votes_remaining=votes_remaining,
        total_votes=total_votes,
        pct_counted=counted_frac,
        n_simulations=config.n_simulations,
        prior_used=prior_val,
        confidence_level=config.confidence_level,
        raw_finals=finals,
        candidate_names=names,
    )
 
def print_results(result: SimulationResult, top_n: int = 10) -> None:
    ci_pct = int(result.confidence_level * 100)
    print(f"\n{'='*70}")
    print(f"  MONTE CARLO ELECTION SIMULATION  —  {result.n_simulations:,} runs")
    print(f"{'='*70}")
    print(f"  Votes counted  : {result.votes_counted:,}  ({result.pct_counted:.1%} of total)")
    print(f"  Votes remaining: {result.votes_remaining:,}")
    print(f"  Total votes    : {result.total_votes:,}")
    print(f"  Prior (δ)      : {result.prior_used}")
    print(f"  Credible int.  : {ci_pct}%")
    print(f"\n  {'Candidate':<45} {'Current':>8} {'Projected':>10} {f'{ci_pct}% CI':>20} {'Win prob':>9} {'Proj. votes':>12} {'Additional':>11}")
    print(f"  {'-'*45} {'-'*8} {'-'*10} {'-'*20} {'-'*9} {'-'*12} {'-'*11}")
    for c in result.candidates[:top_n]:
        ci_str       = f"[{c.ci_low:.2%}, {c.ci_high:.2%}]"
        marker       = " ◀" if c == result.projected_winner else ""
        proj_votes   = int(c.projected_share * result.total_votes)
        additional   = proj_votes - c.votes_counted
        additional_str = f"+{additional:,}" if additional >= 0 else f"{additional:,}"
        print(
            f"  {c.name:<45} {c.current_share:>8.2%} {c.projected_share:>10.2%} "
            f"{ci_str:>20} {c.win_probability:>8.1%} {proj_votes:>12,} {additional_str:>11}"
            f"{marker}"
        )
    if len(result.candidates) > top_n:
        print(f"  ... ({len(result.candidates) - top_n} more candidates)")
    print(f"\n  Projected winner: {result.projected_winner.name}")
    print(f"  Win probability : {result.projected_winner.win_probability:.1%}")
    print(f"  Projected votes : {int(result.projected_winner.projected_share * result.total_votes):,}"
          f"  (+{int(result.projected_winner.projected_share * result.total_votes) - result.projected_winner.votes_counted:,} additional)")
    print(f"{'='*70}\n")

def aggregate_province(district_results: list[SimulationResult]) -> SimulationResult:

    district_results = [r for r in district_results if r is not None]

    n_sim              = district_results[0].n_simulations
    confidence_level   = district_results[0].confidence_level

    all_names    = list(dict.fromkeys(n for r in district_results for n in r.candidate_names))
    name_to_idx  = {name: i for i, name in enumerate(all_names)}
    total_votes  = sum(r.total_votes for r in district_results)

    province_finals = np.zeros((n_sim, len(all_names)))
    for r in district_results:
        for local_idx, name in enumerate(r.candidate_names):
            province_finals[:, name_to_idx[name]] += r.raw_finals[:, local_idx] * r.total_votes
    province_finals /= total_votes

    lo, hi = (1.0 - confidence_level) / 2.0, 1.0 - (1.0 - confidence_level) / 2.0

    means  = province_finals.mean(axis=0)
    stds   = province_finals.std(axis=0)
    ci_lo  = np.quantile(province_finals, lo, axis=0)
    ci_hi  = np.quantile(province_finals, hi, axis=0)

    winner_per_sim = np.argmax(province_finals, axis=1)
    win_probs      = np.bincount(winner_per_sim, minlength=len(all_names)) / n_sim

    name_to_votes = {name: 0 for name in all_names}
    for r in district_results:
        for c in r.candidates:
            name_to_votes[c.name] = name_to_votes.get(c.name, 0) + c.votes_counted

    votes_counted = sum(r.votes_counted for r in district_results)

    candidates = sorted([
        CandidateResult(
            name=all_names[i],
            votes_counted=name_to_votes[all_names[i]],
            current_share=name_to_votes[all_names[i]] / votes_counted if votes_counted else 0.0,
            projected_share=float(means[i]),
            ci_low=float(ci_lo[i]),
            ci_high=float(ci_hi[i]),
            win_probability=float(win_probs[i]),
            std=float(stds[i]),
        )
        for i in range(len(all_names))
    ], key=lambda c: c.projected_share, reverse=True)

    return SimulationResult(
        candidates=candidates,
        projected_winner=candidates[0],
        votes_counted=votes_counted,
        votes_remaining=sum(r.votes_remaining for r in district_results),
        total_votes=total_votes,
        pct_counted=votes_counted / total_votes,
        n_simulations=n_sim,
        prior_used=district_results[0].prior_used,
        confidence_level=confidence_level,
        raw_finals=province_finals,
        candidate_names=all_names,
    )
 