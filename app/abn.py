"""
Australian Business Number checks.

Every Australian message carries the business's ABN in its footer, so a typo
puts a wrong legal identity on every email. The ABN's last digits are a
checksum (the Australian Business Register's published algorithm): take the
11 digits, subtract 1 from the first, multiply each by its weight, and the
sum must divide by 89. This catches nearly every single-digit typo and swap.
"""

WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def digits(text):
    return "".join(ch for ch in str(text or "") if ch.isdigit())


def valid(text):
    d = digits(text)
    if len(d) != 11 or d[0] == "0":
        return False
    nums = [int(c) for c in d]
    nums[0] -= 1
    return sum(n * w for n, w in zip(nums, WEIGHTS)) % 89 == 0


def normalise(text):
    """'51824753556' -> '51 824 753 556'; None when it isn't a valid ABN."""
    if not valid(text):
        return None
    d = digits(text)
    return f"{d[:2]} {d[2:5]} {d[5:8]} {d[8:]}"
