"""Survival-focused mechfight bot."""
import math

import mechfight as mf


def unit(x, y):
    length = math.hypot(x, y)
    if length < 1e-9:
        return 0.0, 0.0
    return x / length, y / length


class Bot:
    name = "oval-survivor"

    def __init__(self, hello):
        self.slot = int(hello["slot"])
        self.config = hello["config"]

    def _zone_heading(self, obs, x, y):
        zone = obs["zone"]
        now = obs["t"]
        center = zone["center"]
        radius = zone["radius"]
        start, end = zone["shrink_start"], zone["shrink_end"]
        if end > start and now >= start:
            f = min(1.0, max(0.0, (now - start) / (end - start)))
            center = [
                center[0] + (zone["next_center"][0] - center[0]) * f,
                center[1] + (zone["next_center"][1] - center[1]) * f,
            ]
            radius += (zone["next_radius"] - radius) * f

        # Drift toward the next circle early; stay well inside the live one.
        next_center = zone["next_center"]
        next_radius = zone["next_radius"]
        dx, dy = next_center[0] - x, next_center[1] - y
        distance = math.hypot(dx, dy)
        desired_radius = max(0.0, min(radius * 0.62, next_radius * 0.62))
        if distance > desired_radius + 10:
            return unit(dx, dy), center, radius, distance > radius - 12

        # Orbit at a moderate radius rather than idling in the center.
        radial = unit(x - center[0], y - center[1])
        if radial == (0.0, 0.0):
            radial = (1.0 if self.slot % 2 == 0 else -1.0, 0.0)
        desired = min(radius * 0.5, next_radius * 0.5)
        correction = max(-0.7, min(0.7, (distance - desired) / max(30.0, radius * 0.4)))
        direction = (
            -radial[1] * (1.0 if self.slot % 2 == 0 else -1.0) - radial[0] * correction,
            radial[0] * (1.0 if self.slot % 2 == 0 else -1.0) - radial[1] * correction,
        )
        return unit(*direction), center, radius, distance > radius - 12

    def _target(self, obs, me):
        candidates = []
        x, y = me["pos"][:2]
        for enemy in obs["mechs"]:
            if enemy["kind"] != "mech":
                continue
            p = enemy["pos"]
            d = math.dist((x, y), p[:2])
            hp = enemy.get("hp", 1000)
            # Low-health targets are useful finishers; nearer enemies are the
            # immediate threat. Weapon-bearing enemies get a small priority.
            weapon_bonus = 0.0
            if "laser" in enemy.get("weapons", []):
                weapon_bonus = 12.0
            score = d + min(80.0, hp * 0.035) - weapon_bonus
            candidates.append((score, d, enemy))
        return min(candidates, default=None, key=lambda item: item[0])

    def _upgrade(self, offer, me):
        priorities = {
            "armor": 100,
            "servos": 92,
            "nanites": 88,
            "plating": 84,
            "jets": 78,
            "reactor": 69,
            "capacitor": 65,
            "sensors": 55,
            "gyros": 48,
            "laser_charge": 76,
            "laser_damage": 74,
            "gun_damage": 60,
            "gun_rate": 58,
            "gun_velocity": 40,
            "lightning_range": 57,
            "lightning_efficiency": 55,
            "lightning_chain": 50,
            "mortar_reload": 50,
            "mortar_splash": 44,
            "mortar_velocity": 35,
            "laser_pierce": 38,
            "gun_mag": 32,
        }
        weapons = {w["kind"] for w in me["weapons"] if w}
        best = 0
        best_score = -1
        for i, choice in enumerate(offer["choices"]):
            key = choice["id"]
            score = priorities.get(key, 0)
            if key.startswith("laser_") and "laser" not in weapons:
                score -= 40
            elif key.startswith("lightning_") and "lightning" not in weapons:
                score -= 40
            elif key.startswith("mortar_") and "mortar" not in weapons:
                score -= 40
            if score > best_score:
                best, best_score = i, score
        return best

    def act(self, obs):
        me = obs["self"]
        x, y, z = me["pos"]
        action = {"tick": obs["tick"]}

        if obs["offer"]:
            action["pick"] = self._upgrade(obs["offer"], me)

        zone_dir, zone_center, zone_radius, urgent_zone = self._zone_heading(obs, x, y)
        target_data = self._target(obs, me)
        target = target_data[2] if target_data else None
        target_dist = target_data[1] if target_data else float("inf")

        threat_yaw = None
        if target is not None:
            threat_yaw = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
        else:
            recent = [
                e for e in obs["events"]
                if e["type"] == "damaged" and e.get("bearing") is not None
            ]
            if recent:
                threat_yaw = recent[-1]["bearing"]
            else:
                sounds = [
                    s for s in obs["sounds"]
                    if s["kind"] in ("gun", "mortar", "explosion", "laser",
                                     "laser_charge", "lightning", "npc_gun")
                    and s["age"] < 1.0
                ]
                if sounds:
                    ranges = {"near": 25.0, "mid": 100.0, "far": 220.0}
                    sound = min(sounds, key=lambda s: ranges[s["dist"]] + s["age"] * 45)
                    threat_yaw = sound["bearing"]

        # Evasive motion: hold the zone, orbit, and break a pursuer's straight
        # lead with a sideways component that reverses periodically.
        mx, my = zone_dir
        if target is not None and target_dist < 145:
            away = unit(x - target["pos"][0], y - target["pos"][1])
            side = 1.0 if (int(obs["t"] / 2.4) + self.slot) % 2 == 0 else -1.0
            urgency = max(0.0, min(1.5, (145.0 - target_dist) / 75.0))
            mx += urgency * (1.4 * away[0] - 0.65 * side * away[1])
            my += urgency * (1.4 * away[1] + 0.65 * side * away[0])
        elif threat_yaw is not None:
            # A bearing-only threat should not pull us out of the circle.
            d = {"near": 0.85, "mid": 0.35, "far": 0.0}
            weight = 0.25
            for sound in obs["sounds"]:
                if sound["bearing"] == threat_yaw:
                    weight = d.get(sound["dist"], 0.0)
                    break
            if weight:
                mx -= math.cos(threat_yaw) * weight
                my -= math.sin(threat_yaw) * weight
        mx, my = unit(mx, my)
        action["move"] = [mx, my]

        energy = me["energy"]
        action["boost"] = bool(
            energy > 24 and (urgent_zone or (target is not None and target_dist < 95))
        )
        action["jump"] = bool(me["grounded"] and target is not None
                              and target_dist < 35 and obs["tick"] % 20 == 0)

        # Face the closest attacker even when not shooting; the forward arc
        # takes less damage than exposing the rear armor.
        aim_yaw = threat_yaw if threat_yaw is not None else math.atan2(zone_center[1] - y, zone_center[0] - x)
        desired_pitch = 0.0
        if target is not None:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target["vel"]
            horizontal = max(1.0, math.hypot(tx - x, ty - y))
            weapons = me["weapons"]
            gun = weapons[0]
            use_laser = len(weapons) > 1 and weapons[1] and weapons[1]["kind"] == "laser"
            if gun and gun["kind"] == "gun":
                flight = horizontal / max(1.0, gun["stats"]["speed"])
            elif use_laser:
                flight = 0.0
            else:
                flight = 0.0
            px = tx + vx * flight - x
            py = ty + vy * flight - y
            pz = tz + 4.5 + vz * flight - (z + me["stats"].get("muzzle_height", 7.0))
            aim_yaw = math.atan2(py, px)
            desired_pitch = math.atan2(pz, max(1.0, math.hypot(px, py)))
        action["aim"] = [aim_yaw, desired_pitch]

        weapons = me["weapons"]
        fire = [False, False]
        hp = me["hp"]
        healthy_enough_to_fight = hp >= me["max_hp"] * 0.78
        emergency = target is not None and target_dist < 42
        can_attack = target is not None and (healthy_enough_to_fight or emergency)

        if len(weapons) > 0 and weapons[0]:
            gun = weapons[0]
            if gun["kind"] == "gun" and can_attack:
                ammo = gun.get("ammo", 0)
                if ammo <= 2 and gun.get("reload", 0) <= 0:
                    action["reload"] = True
                else:
                    fire[0] = True
            elif gun["kind"] == "laser" and can_attack:
                charge = gun.get("charge", 0.0)
                if charge >= gun["stats"].get("charge", 0.75) * 0.88:
                    fire[0] = False
                else:
                    fire[0] = True
            elif gun["kind"] == "lightning" and can_attack:
                fire[0] = target_dist <= gun["stats"].get("range", 45.0)
            elif gun["kind"] == "mortar" and can_attack:
                fire[0] = target_dist < 145

        if len(weapons) > 1 and weapons[1]:
            weapon = weapons[1]
            if weapon["kind"] == "laser":
                charge = weapon.get("charge", 0.0)
                if can_attack:
                    fire[1] = charge < weapon["stats"].get("charge", 0.75) * 0.88
            elif weapon["kind"] == "lightning" and can_attack:
                fire[1] = target_dist <= weapon["stats"].get("range", 45.0)
            elif weapon["kind"] == "mortar" and can_attack:
                fire[1] = target_dist < 145

        if any(fire):
            action["fire"] = fire
        elif len(weapons) > 1:
            action["fire"] = fire

        # Slot 1 is empty at spawn. Prefer laser for ranged fights, then
        # lightning; avoid mortar because self-splash and laser cook-off are
        # especially punishing in a survival-scored match.
        held = {w["kind"] for w in weapons if w}
        preferences = {"laser": 3, "lightning": 2, "mortar": 1, "gun": 0}
        candidates = []
        for pickup in obs["pickups"]:
            d = math.dist((x, y), pickup["pos"][:2])
            if d <= self.config["loot"]["pickup_radius"] and abs(pickup["pos"][2] - z) <= 4:
                if pickup["kind"] not in held:
                    candidates.append((preferences[pickup["kind"]], d, pickup))
        if candidates:
            _, _, pickup = max(candidates, key=lambda item: (item[0], -item[1]))
            if not weapons[1]:
                action["take"] = pickup["id"]
            elif preferences[pickup["kind"]] > preferences[weapons[1]["kind"]]:
                action["take"] = pickup["id"]
                action["drop"] = 1

        return action


mf.run(Bot)
