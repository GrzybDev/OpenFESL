from openfesl.core.reward_rules import ReferralStep
from openfesl.core.rewards import Referred, Reward, RewardState
from openfesl.web.reward_texts import RewardTexts

# The number players see each step by; the sign-up is step 1.
NUMBERS = {"first": 2, "second": 3}


def band(words: RewardTexts, reward: Reward) -> str:
    """Return whom a step's reward is for: the recruit it counts, or who recruited the account.

    Args:
        words: What the page says of the rewards.
        reward: The reward, of a referral step.
    """
    referral, _ = _step(words, reward)
    recruit, number = referral.recruit, NUMBERS[referral.step]
    if referral.recruiter:
        name = recruit.account.display_name or ""
        return words.texts.text("web.rewards.referral.band.recruit", name=name, n=number)
    # A recruiter that other accounts no longer see, as a banned one, goes unnamed.
    key = "recruited_by" if recruit.recruiter.public else "recruited"
    name = recruit.recruiter.display_name or ""
    return words.texts.text(f"web.rewards.referral.band.{key}", name=name, n=number)


def story(words: RewardTexts, reward: Reward) -> str:
    """Return what reaching a step means, or when it was reached or ended.

    Args:
        words: What the page says of the rewards.
        reward: The reward, of a referral step.
    """
    referral, step = _step(words, reward)
    texts, recruit = words.texts, referral.recruit
    whose = "" if referral.recruiter else ".own"
    name = recruit.account.display_name or ""
    ends = step.ends(recruit.joined)
    values = {"name": name, "level": step.level, "days": step.days or 0}
    if reach := recruit.reached.get(referral.step):
        within = ".within" if ends else ""
        said = texts.text(
            f"web.rewards.referral.reached{whose}{within}",
            **values,
            hero=reach.hero,
            date=words.day(reach.at),
        )
        if reward.gift.for_hero and not referral.recruiter:
            said += " " + texts.text("web.rewards.referral.hero_rule", level=step.level)
        return said
    if ends and reward.state is RewardState.EXPIRED:
        return texts.text(f"web.rewards.referral.over{whose}", **values, date=words.day(ends))
    if ends:
        return texts.text(f"web.rewards.referral.when{whose}.by", **values, date=words.day(ends))
    return texts.text(f"web.rewards.referral.when{whose}", **values)


def standing(words: RewardTexts, reward: Reward) -> tuple[str, str]:
    """Return a step's state line and its kind: ready, waiting with its end, claimed or ended.

    Args:
        words: What the page says of the rewards.
        reward: The reward, of a referral step.
    """
    referral, step = _step(words, reward)
    texts, recruit = words.texts, referral.recruit
    ends = step.ends(recruit.joined)
    if reward.claim:
        return words.given(reward.claim), "claimed"
    if reward.state is RewardState.CLAIMABLE:
        return texts.text("web.rewards.referral.ready"), "ready"
    if referral.step in recruit.reached:
        return texts.text("web.rewards.state.unfit"), "locked"
    if ends and reward.state is RewardState.EXPIRED:
        level = recruit.leader.level if recruit.leader else 0
        # A hero that passed the goal after the days ended, or unseen, names no level.
        key = "web.rewards.referral.ended" + (".level" if level < step.level else "")
        return texts.text(key, date=words.day(ends), level=level), "expired"
    if ends:
        return texts.text("web.rewards.referral.locked.until", date=words.day(ends)), "locked"
    return texts.text("web.rewards.referral.locked"), "locked"


def lead(words: RewardTexts, rewards: list[Reward]) -> str:
    """Return what the section of Recruit a Friend says of the account's recruits and recruiter.

    Args:
        words: What the page says of the rewards.
        rewards: The rewards the account sees now.
    """
    referred = [reward.referral for reward in rewards if reward.referral]
    said = []
    if any(referral.recruiter for referral in referred):
        said.append(words.texts.text("web.rewards.referral.lead.recruiter"))
    if mine := next((r.recruit for r in referred if not r.recruiter), None):
        key = "recruit" if mine.recruiter.public else "recruited"
        name = mine.recruiter.display_name or ""
        said.append(words.texts.text(f"web.rewards.referral.lead.{key}", name=name))
    return " ".join(said)


def _step(words: RewardTexts, reward: Reward) -> tuple[Referred, ReferralStep]:
    referral, rules = reward.referral, words.rules.referral
    assert referral is not None and rules is not None
    return referral, rules.steps[referral.step]
