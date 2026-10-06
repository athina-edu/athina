"""Random team-name generation.

Team names are only labels — the repository is named after the assignment and
the group number — but they still need to be unique per assignment (enforced by
StudentGroup's unique_together), so generated names must avoid the existing set.

Names are short and pronounceable so they are easy to say in a lecture hall and
to scan in a list. The word lists are deliberately uncontroversial.
"""

import random

ADJECTIVES = (
    'Amber', 'Azure', 'Bold', 'Brave', 'Bright', 'Bronze', 'Calm', 'Clever',
    'Cobalt', 'Crimson', 'Crisp', 'Daring', 'Eager', 'Emerald', 'Fleet', 'Gentle',
    'Golden', 'Grand', 'Hidden', 'Indigo', 'Iron', 'Jade', 'Keen', 'Lively',
    'Lucid', 'Mellow', 'Nimble', 'Noble', 'Onyx', 'Quiet', 'Rapid', 'Royal',
    'Ruby', 'Sharp', 'Silent', 'Silver', 'Sleek', 'Solar', 'Steady', 'Sterling',
    'Swift', 'Teal', 'Vivid', 'Warm', 'Wild', 'Wise',
)

NOUNS = (
    'Anchor', 'Arrow', 'Badger', 'Beacon', 'Bison', 'Bridge', 'Cascade', 'Cedar',
    'Comet', 'Compass', 'Condor', 'Coral', 'Delta', 'Dolphin', 'Dragon', 'Eagle',
    'Ember', 'Falcon', 'Forge', 'Fox', 'Garnet', 'Glacier', 'Harbor', 'Heron',
    'Lynx', 'Meadow', 'Meridian', 'Nimbus', 'Orchid', 'Osprey', 'Otter', 'Panther',
    'Phoenix', 'Pilot', 'Quarry', 'Raven', 'Reef', 'Ridge', 'River', 'Sapphire',
    'Sentinel', 'Summit', 'Swan', 'Thunder', 'Titan', 'Voyager', 'Willow', 'Zephyr',
)

# Distinct names available before any word pair repeats.
COMBINATION_COUNT = len(ADJECTIVES) * len(NOUNS)


def random_team_name(taken=()):
    """A team name not present in `taken` (case-insensitive).

    Picks a random adjective+noun pair. Falls back to a numeric suffix if the
    name space is exhausted — with ~2,000 combinations that only happens for a
    deliberately enormous course, but returning None would leave the caller
    with an unusable form.
    """
    taken_lower = {str(name).strip().lower() for name in taken if name}

    for _attempt in range(COMBINATION_COUNT):
        name = "%s %s" % (random.choice(ADJECTIVES), random.choice(NOUNS))
        if name.lower() not in taken_lower:
            return name

    # Exhausted: every pair is taken. Fall back to a guaranteed-unique suffix.
    suffix = 2
    base = "%s %s" % (random.choice(ADJECTIVES), random.choice(NOUNS))
    while ("%s %d" % (base, suffix)).lower() in taken_lower:
        suffix += 1
    return "%s %d" % (base, suffix)
