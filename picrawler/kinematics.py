"""Leg kinematics for the PiCrawler.

Pure math, no hardware access. A leg has three segments: C (coxa, hip to
femur joint), A (femur) and B (tibia), all in mm. Angles are in degrees:
alpha = femur, beta = tibia (knee), gamma = hip rotation.

Ordering pitfall kept from the original library: coord2polar() returns
[alpha, beta, gamma], but the servos of one leg are wired in the order
[beta, alpha, gamma]. polar2coord() and limit_angle() take angles in that
servo order. Use leg_servo_angles() to convert a coordinate to servo order.
"""
import math

A = 48
B = 78
C = 33

# Per-leg servo limits in servo order [knee, femur, hip].
SERVO_LIMITS = ((-90, 90), (-10, 90), (-60, 60))


def clamp_coord(coord, a=A, b=B, c=C):
    """Scale a leg-tip coordinate into the reachable shell [c, a+b+c]."""
    x, y, z = coord

    L = math.sqrt(x**2 + y**2 + z**2)
    if L == 0:
        L = 0.1
    if L < c:
        temp = c / L
        x = temp * x
        y = temp * y
        z = temp * z
    elif L > (a + b + c):
        temp = (a + b + c) / L
        x = temp * x
        y = temp * y
        z = temp * z
    return [x, y, z]


def coord2polar(coord, a=A, b=B, c=C):
    """Inverse kinematics: leg-tip [x, y, z] -> [alpha, beta, gamma]."""
    x, y, z = clamp_coord(coord, a, b, c)

    w = math.sqrt(math.pow(x, 2) + math.pow(y, 2))
    v = w - c
    u = math.sqrt(math.pow(z, 2) + math.pow(v, 2))
    u = max(30, min(91.58, u))
    cos_angle1 = (b**2 + a**2 - u**2) / (2 * b * a)
    beta = math.acos(cos_angle1)

    angle1 = math.atan2(z, v)
    angle2 = math.acos((a**2 + u**2 - b**2) / (2 * a * u))
    alpha = angle2 + angle1

    gamma = math.atan2(y, x)

    alpha = 90 - alpha / math.pi * 180
    beta = beta / math.pi * 180 - 90
    gamma = -(gamma / math.pi * 180 - 45)

    return [round(alpha, 4), round(beta, 4), round(gamma, 4)]


def leg_servo_angles(coord, a=A, b=B, c=C):
    """Leg-tip [x, y, z] -> servo-order angles [beta, alpha, gamma]."""
    alpha, beta, gamma = coord2polar(coord, a, b, c)
    return [beta, alpha, gamma]


def polar2coord(angles, a=A, b=B, c=C):
    """Forward kinematics: servo-order [beta, alpha, gamma] -> [x, y, z].

    The parameter names below follow the original code, where "alpha" is
    the knee servo and "beta" the femur servo.
    """
    alpha, beta, gamma = angles

    L1 = math.sqrt(a**2 + b**2 - 2 * a * b * math.cos((90 + alpha) / 180 * math.pi))
    angle = math.acos((a**2 + L1**2 - b**2) / (2 * a * L1)) * 180 / math.pi
    angle = 90 - beta - angle
    L = L1 * math.cos(angle * math.pi / 180) + c

    x = L * math.sin((45 + gamma) * math.pi / 180)
    y = L * math.cos((45 + gamma) * math.pi / 180)
    z = L1 * math.sin(angle * math.pi / 180)

    return [round(x, 4), round(y, 4), round(z, 4)]


def limit(min, max, x):
    if x > max:
        return max
    elif x < min:
        return min
    else:
        return x


def limit_angle(angles):
    """Clamp servo-order angles [beta, alpha, gamma] to SERVO_LIMITS.

    Returns (limited, angles) where limited is True if any value was clamped.
    """
    limited = False
    result = []
    for value, (lo, hi) in zip(angles, SERVO_LIMITS):
        temp = limit(lo, hi, value)
        if temp != value:
            limited = True
        result.append(temp)
    return limited, result
