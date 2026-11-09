"""Descriptive intervals; paired means retain the seed as sampling unit."""
import math
import random
import statistics


def wilson(k, n):
    if n == 0:
        return [None, None]
    z = 1.959963984540054
    p = k / n
    d = 1 + z*z/n
    center = (p + z*z/(2*n))/d
    margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return [max(0, center - margin), min(1, center + margin)]


def paired_bootstrap(differences, replicates=2000):
    if not differences:
        return {'mean': None, 'ci95': [None, None]}
    rng = random.Random(74021)
    n = len(differences)
    means = sorted(sum(differences[rng.randrange(n)] for _ in range(n))/n for _ in range(replicates))
    return {'mean': statistics.mean(differences), 'ci95': [means[int(.025*replicates)], means[int(.975*replicates)-1]]}
