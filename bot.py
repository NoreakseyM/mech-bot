"""Survival-focused mechfight bot."""
import math

import mechfight as mf

WEAPON_PREFERENCE = {"laser": 3, "lightning": 2, "mortar": 1, "gun": 0}
SOUND_RANGES = {"near": 25.0, "mid": 100.0, "far": 220.0}
SOUND_WEIGHTS = {"near": 0.8, "mid": 0.4, "far": 0.12}
THREAT_SOUNDS = {
    "gun", "mortar", "explosion", "laser", "laser_charge", "lightning",
    "npc_gun", "boost", "jet", "step",
}


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
        self.last_damaged_t = -999.0

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

        next_center = zone["next_center"]
        next_radius = zone["next_radius"]
        dx, dy = next_center[0] - x, next_center[1] - y
        distance = math.hypot(dx, dy)
        current_distance = math.hypot(x - center[0], y - center[1])
        desired_radius = max(0.0, min(radius * 0.62, next_radius * 0.62))
        urgent = current_distance > radius - 24
        if distance > desired_radius + 10:
            return unit(dx, dy), center, radius, urgent

        radial = unit(x - next_center[0], y - next_center[1])
        if radial == (0.0, 0.0):
            radial = (1.0 if self.slot % 2 == 0 else -1.0, 0.0)
        desired = next_radius * 0.5
        correction = max(-0.7, min(0.7, (distance - desired) / max(30.0, next_radius * 0.4)))
        direction = (
            -radial[1] * (1.0 if self.slot % 2 == 0 else -1.0) - radial[0] * correction,
            radial[0] * (1.0 if self.slot % 2 == 0 else -1.0) - radial[1] * correction,
        )
        return unit(*direction), center, radius, urgent

    def _target(self, obs, me):
        x, y = me["pos"][:2]
        best = None
        best_score = math.inf
        for enemy in obs["mechs"]:
            if enemy["kind"] != "mech":
                continue
            p = enemy["pos"]
            d = math.hypot(x - p[0], y - p[1])
            hp = enemy.get("hp", 1000)
            weapon_bonus = 0.0
            if "laser" in enemy.get("weapons", []):
                weapon_bonus += 12.0
            if enemy.get("charging"):
                weapon_bonus += 18.0
            score = d + min(105.0, hp * 0.075) - weapon_bonus
            if score < best_score:
                best = (score, d, enemy)
                best_score = score
        return best

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
        weapons = me["weapons"]
        action = {"tick": obs["tick"]}

        for event in obs["events"]:
            if event["type"] == "damaged" and event.get("weapon") != "zone":
                self.last_damaged_t = obs["t"]
        under_fire = obs["t"] - self.last_damaged_t < 4.0

        if obs["offer"]:
            action["pick"] = self._upgrade(obs["offer"], me)

        zone_dir, zone_center, zone_radius, urgent_zone = self._zone_heading(obs, x, y)
        target_data = self._target(obs, me)
        target = target_data[2] if target_data else None
        target_dist = target_data[1] if target_data else float("inf")

        threat_yaw = None
        threat_weight = 0.0
        if target is not None:
            threat_yaw = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
            threat_weight = 1.0
        else:
            for event in obs["events"]:
                bearing = event.get("bearing")
                if event["type"] == "damaged" and bearing is not None:
                    threat_yaw = bearing
            if threat_yaw is not None:
                threat_weight = 0.75
            else:
                best_sound_score = math.inf
                for sound in obs["sounds"]:
                    if sound["kind"] not in THREAT_SOUNDS or sound["age"] >= 1.5:
                        continue
                    score = SOUND_RANGES[sound["dist"]] + sound["age"] * 45
                    if score < best_sound_score:
                        best_sound_score = score
                        threat_yaw = sound["bearing"]
                        threat_weight = SOUND_WEIGHTS[sound["dist"]]

        pickup_radius = self.config["loot"]["pickup_radius"]
        slot0 = weapons[0] if weapons else None
        slot1 = weapons[1] if len(weapons) > 1 else None
        held0 = slot0["kind"] if slot0 else None
        held1 = slot1["kind"] if slot1 else None
        held1_preference = WEAPON_PREFERENCE[held1] if held1 else -1
        loot_target = None
        loot_key = None
        pickup_choice = None
        pickup_key = None
        pickup_radius_sq = pickup_radius * pickup_radius
        for pickup in obs["pickups"]:
            kind = pickup["kind"]
            preference = WEAPON_PREFERENCE[kind]
            if kind == held0 or kind == held1 or preference <= held1_preference:
                continue
            px, py, pz = pickup["pos"]
            dx, dy = px - x, py - y
            distance_sq = dx * dx + dy * dy
            if distance_sq <= 90 * 90:
                distance = math.sqrt(distance_sq)
                candidate_key = (distance, -preference)
                if loot_key is None or candidate_key < loot_key:
                    loot_target = (distance, pickup)
                    loot_key = candidate_key
                if distance_sq <= pickup_radius_sq and abs(pz - z) <= 4:
                    candidate_key = (preference, -distance_sq)
                    if pickup_key is None or candidate_key > pickup_key:
                        pickup_choice = pickup
                        pickup_key = candidate_key

        mx, my = zone_dir
        if target is not None and target_dist < 175:
            away = unit(x - target["pos"][0], y - target["pos"][1])
            side = 1.0 if (int(obs["t"] / 2.4) + self.slot) % 2 == 0 else -1.0
            urgency = max(0.15, min(1.7, (175.0 - target_dist) / 75.0))
            mx += urgency * (1.25 * away[0] - 0.7 * side * away[1])
            my += urgency * (1.25 * away[1] + 0.7 * side * away[0])
        elif target is not None:
            side = 1.0 if (int(obs["t"] / 2.4) + self.slot) % 2 == 0 else -1.0
            away = unit(x - target["pos"][0], y - target["pos"][1])
            mx -= 0.35 * side * away[1]
            my += 0.35 * side * away[0]
        elif threat_yaw is not None:
            mx -= math.cos(threat_yaw) * threat_weight
            my -= math.sin(threat_yaw) * threat_weight

        loot_distance = float("inf")
        if loot_target is not None and (target is None or target_dist > 100) and not urgent_zone:
            loot_distance, pickup = loot_target
            toward_loot = unit(pickup["pos"][0] - x, pickup["pos"][1] - y)
            weight = 1.1 * (1.0 - loot_distance / 90.0)
            mx += weight * toward_loot[0]
            my += weight * toward_loot[1]
        mx, my = unit(mx, my)
        calm = (
            target is None and threat_yaw is None and not under_fire
            and not urgent_zone and loot_target is None
        )
        if calm:
            nc = obs["zone"]["next_center"]
            if math.hypot(x - nc[0], y - nc[1]) < obs["zone"]["next_radius"] * 0.7:
                mx *= 0.4
                my *= 0.4
        action["move"] = [mx, my]

        energy = me["energy"]
        action["boost"] = bool(
            energy > 35 and (
                urgent_zone
                or (target is not None and target_dist < 105)
                or (target is None and loot_distance < 65)
            )
        )
        action["jump"] = bool(me["grounded"] and target is not None
                              and target_dist < 35 and obs["tick"] % 20 == 0)

        # Face the closest attacker even when not shooting; the forward arc
        # takes less damage than exposing the rear armor.
        scan_yaw = (obs["t"] * 1.6 + self.slot * (2 * math.pi / 16) + math.pi) % (2 * math.pi) - math.pi
        aim_yaw = threat_yaw if threat_yaw is not None else scan_yaw
        desired_pitch = 0.0
        if target is not None:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target["vel"]
            horizontal = max(1.0, math.hypot(tx - x, ty - y))
            gun = weapons[0]
            laser = next((w for w in weapons if w and w["kind"] == "laser"), None)
            laser_ready = laser and target_dist <= laser["stats"].get("range", 280.0) and (
                laser.get("charge", 0.0) > 0 or laser["cooldown"] <= 0
            )
            if laser_ready:
                flight = 0.0
            elif gun and gun["kind"] == "gun":
                flight = horizontal / max(1.0, gun["stats"]["speed"])
            else:
                flight = 0.0
            px = tx + vx * flight - x
            py = ty + vy * flight - y
            pz = tz + 4.5 + vz * flight - (z + me["stats"].get("muzzle_height", 7.0))
            aim_yaw = math.atan2(py, px)
            desired_pitch = math.atan2(pz, max(1.0, math.hypot(px, py)))
        action["aim"] = [aim_yaw, desired_pitch]

        fire = [False, False]
        can_attack = target is not None and (target_dist < 80 or under_fire)

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
                if target_dist <= gun["stats"].get("range", 280.0) and charge >= gun["stats"].get("charge", 0.75) * 0.88:
                    fire[0] = False
                elif target_dist <= gun["stats"].get("range", 280.0):
                    fire[0] = True
            elif gun["kind"] == "lightning" and can_attack:
                fire[0] = target_dist <= gun["stats"].get("range", 45.0)
            elif gun["kind"] == "mortar" and can_attack:
                fire[0] = 20 < target_dist < 60

        if len(weapons) > 1 and weapons[1]:
            weapon = weapons[1]
            if weapon["kind"] == "laser":
                charge = weapon.get("charge", 0.0)
                if can_attack and target_dist <= weapon["stats"].get("range", 280.0):
                    fire[1] = charge < weapon["stats"].get("charge", 0.75) * 0.88
            elif weapon["kind"] == "lightning" and can_attack:
                fire[1] = target_dist <= weapon["stats"].get("range", 45.0)
            elif weapon["kind"] == "mortar" and can_attack:
                fire[1] = 20 < target_dist < 60

        if any(fire):
            action["fire"] = fire
        elif len(weapons) > 1:
            action["fire"] = fire

        # Slot 1 is empty at spawn. Prefer laser for ranged fights, then
        # lightning; avoid mortar because self-splash and laser cook-off are
        # especially punishing in a survival-scored match.
        if pickup_choice is not None:
            action["take"] = pickup_choice["id"]
            if slot1:
                action["drop"] = 1

        return action


mf.run(Bot)
