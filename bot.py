"""Survival-focused mechfight bot."""
import array
import json
import math
import os
import sys

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


def angle_delta(a, b):
    return math.atan2(math.sin(a - b), math.cos(a - b))


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Bot:
    name = "oval-survivor"

    def __init__(self, hello):
        self.slot = int(hello["slot"])
        self.config = hello["config"]
        world_path = os.path.join(hello["map"]["dir"], "v2", "world.json")
        with open(world_path, encoding="utf-8") as world_file:
            world = json.load(world_file)
        terrain = world["terrain"]
        terrain_path = os.path.join(hello["map"]["dir"], "v2", terrain["file"])
        with open(terrain_path, "rb") as terrain_file:
            terrain_values = array.array("h")
            terrain_values.frombytes(terrain_file.read())
        if sys.byteorder != "little":
            terrain_values.byteswap()
        if len(terrain_values) != terrain["nx"] * terrain["ny"]:
            raise ValueError("arena terrain size does not match world metadata")
        self.terrain = (
            terrain_values, terrain["nx"], terrain["ny"],
            terrain["minX"], terrain["minY"], terrain["step"], terrain["scale"],
        )
        self.solids = []
        for prism in world["prisms"]:
            ring = list(zip(prism["ring"][::2], prism["ring"][1::2]))
            holes = [
                list(zip(hole[::2], hole[1::2]))
                for hole in prism.get("holes", [])
            ]
            self.solids.append((
                prism["base"], prism["top"], ring, holes,
                min(x for x, _ in ring), max(x for x, _ in ring),
                min(y for _, y in ring), max(y for _, y in ring),
            ))

    def _line_blocked(self, start, end):
        x0, y0, z0 = start
        x1, y1, z1 = end
        dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
        ray_min_x, ray_max_x = min(x0, x1), max(x0, x1)
        ray_min_y, ray_max_y = min(y0, y1), max(y0, y1)
        for base, top, ring, holes, min_x, max_x, min_y, max_y in self.solids:
            if (max_x < ray_min_x or min_x > ray_max_x
                    or max_y < ray_min_y or min_y > ray_max_y):
                continue
            cuts = [0.0, 1.0]
            for polygon in (ring, *holes):
                for i, (ax, ay) in enumerate(polygon):
                    bx, by = polygon[(i + 1) % len(polygon)]
                    ex, ey = bx - ax, by - ay
                    denominator = dx * ey - dy * ex
                    if abs(denominator) < 1e-9:
                        continue
                    qx, qy = ax - x0, ay - y0
                    t = (qx * ey - qy * ex) / denominator
                    u = (qx * dy - qy * dx) / denominator
                    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
                        cuts.append(t)
            cuts.sort()
            for low, high in zip(cuts, cuts[1:]):
                if high - low < 1e-8:
                    continue
                middle = (low + high) * 0.5
                px, py = x0 + dx * middle, y0 + dy * middle
                if not point_in_ring(px, py, ring):
                    continue
                if any(point_in_ring(px, py, hole) for hole in holes):
                    continue
                z_low = z0 + dz * low
                z_high = z0 + dz * high
                if max(z_low, z_high) >= base and min(z_low, z_high) <= top:
                    return True
        terrain = self.terrain
        if terrain is not None:
            values, nx, ny, min_x, min_y, step, scale = terrain
            distance = math.hypot(dx, dy)
            samples = max(2, int(distance / max(0.5, step * 0.5)))
            for sample in range(1, samples):
                t = sample / samples
                px, py, pz = x0 + dx * t, y0 + dy * t, z0 + dz * t
                gx, gy = (px - min_x) / step, (py - min_y) / step
                if gx < 0 or gy < 0 or gx > nx - 1 or gy > ny - 1:
                    continue
                ix, iy = min(nx - 2, int(gx)), min(ny - 2, int(gy))
                fx, fy = gx - ix, gy - iy
                row = iy * nx
                h00 = values[row + ix] * scale
                h10 = values[row + ix + 1] * scale
                h01 = values[row + nx + ix] * scale
                h11 = values[row + nx + ix + 1] * scale
                height = (
                    (h00 * (1.0 - fx) + h10 * fx) * (1.0 - fy)
                    + (h01 * (1.0 - fx) + h11 * fx) * fy
                )
                if pz <= height + 0.05:
                    return True
        return False

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
            return unit(dx, dy), urgent

        radial = unit(x - next_center[0], y - next_center[1])
        if radial == (0.0, 0.0):
            radial = (1.0 if self.slot % 2 == 0 else -1.0, 0.0)
        desired = next_radius * 0.5
        correction = max(-0.7, min(0.7, (distance - desired) / max(30.0, next_radius * 0.4)))
        direction = (
            -radial[1] * (1.0 if self.slot % 2 == 0 else -1.0) - radial[0] * correction,
            radial[0] * (1.0 if self.slot % 2 == 0 else -1.0) - radial[1] * correction,
        )
        return unit(*direction), urgent

    def _target(self, obs, me, attacker_id=None):
        x, y = me["pos"][:2]
        best = None
        best_score = math.inf
        for enemy in obs["mechs"]:
            if enemy["kind"] not in ("mech", "npc"):
                continue
            p = enemy["pos"]
            d = math.hypot(x - p[0], y - p[1])
            if enemy["kind"] == "npc" and d > 55 and enemy.get("id") != attacker_id:
                continue
            hp = enemy.get("hp", 1000)
            score = d + min(105.0, hp * 0.075)
            if "laser" in enemy.get("weapons", []):
                score -= 12.0
            if enemy.get("charging"):
                score -= 18.0
            if enemy["kind"] == "npc":
                score += 45.0
            if enemy.get("id") == attacker_id:
                score -= 250.0
            if score < best_score:
                best = (score, d, enemy)
                best_score = score
        return best

    def _drop_goal(self, obs, me, target_dist):
        x, y = me["pos"][:2]
        me_id = me["id"]
        best = None
        best_score = math.inf
        walk_speed = max(1.0, me["stats"].get("walk_speed", 11.7))
        health_ratio = me["hp"] / max(1.0, me["max_hp"])
        energy_ratio = me["energy"] / max(1.0, me["max_energy"])

        for drop in obs["drops"]:
            dx, dy = drop["pos"][0] - x, drop["pos"][1] - y
            distance = math.hypot(dx, dy)
            if drop["holder"] == me_id:
                return drop, distance
            if drop["holder"] is not None or distance > 190 or target_dist < 105:
                continue
            if any(
                math.hypot(actor["pos"][0] - drop["pos"][0],
                           actor["pos"][1] - drop["pos"][1]) < drop["radius"] + 28
                for actor in obs["mechs"]
            ):
                continue
            travel_time = max(0.0, distance - drop["radius"]) / max(4.0, walk_speed * 0.75)
            if drop["lands_in"] > travel_time + 9.0:
                continue
            if health_ratio > 0.92 and energy_ratio > 0.8 and me["cubes"] >= 6 and distance > 65:
                continue
            score = distance + max(0.0, drop["lands_in"] - travel_time) * 0.15
            if health_ratio < 0.55:
                score -= 30.0
            if score < best_score:
                best = (drop, distance)
                best_score = score
        return best

    def _cube_goal(self, obs, me, target_dist):
        if me["cubes"] >= 10 or target_dist < 110:
            return None
        x, y, z = me["pos"]
        best = None
        best_distance = 70.0
        for cube in obs["cubes"]:
            px, py, pz = cube["pos"]
            distance = math.hypot(px - x, py - y)
            if distance >= best_distance or abs(pz - z) > 4:
                continue
            if any(
                actor["kind"] == "mech"
                and math.hypot(actor["pos"][0] - px, actor["pos"][1] - py) < 24
                for actor in obs["mechs"]
            ):
                continue
            best, best_distance = cube, distance
        return (best, best_distance) if best is not None else None

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
        health_ratio = me["hp"] / max(1.0, me["max_hp"])
        energy_ratio = me["energy"] / max(1.0, me["max_energy"])
        if health_ratio < 0.7:
            priorities["plating"] += 20
            priorities["nanites"] += 15
            priorities["armor"] += 10
        if energy_ratio < 0.4:
            priorities["capacitor"] += 20
            priorities["reactor"] += 12
            priorities["jets"] += 8
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

        if obs["offer"]:
            action["pick"] = self._upgrade(obs["offer"], me)

        incoming = None
        for event in obs["events"]:
            if event["type"] == "damaged" and event.get("bearing") is not None:
                if incoming is None or event["amount"] >= incoming["amount"]:
                    incoming = event
        attacker_id = incoming.get("by") if incoming else None
        zone_dir, urgent_zone = self._zone_heading(obs, x, y)
        target_data = self._target(obs, me, attacker_id)
        target = target_data[2] if target_data else None
        target_dist = target_data[1] if target_data else float("inf")
        target_height = (
            1.75 if target["kind"] == "npc"
            else 4.5 * (1.0 + 0.05 * target.get("cubes", 0))
        ) if target is not None else 0.0

        threat_yaw = None
        threat_weight = 0.0
        if incoming is not None and (
            target is None or attacker_id is None or target["id"] != attacker_id
        ):
            threat_yaw = incoming["bearing"]
            threat_weight = 1.0
        elif target is not None:
            threat_yaw = math.atan2(target["pos"][1] - y, target["pos"][0] - x)
            threat_weight = 1.0
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
        enemy_weapons = set(target.get("weapons", [])) if target else set()
        counter_priority = dict(WEAPON_PREFERENCE)
        if "laser" in enemy_weapons:
            counter_priority["lightning"] = 4
        elif "lightning" in enemy_weapons:
            counter_priority["mortar"] = 4
        elif "mortar" in enemy_weapons:
            counter_priority["laser"] = 4
        held1_preference = counter_priority[held1] if held1 else -1
        loot_target = None
        loot_key = None
        pickup_choice = None
        pickup_key = None
        pickup_radius_sq = pickup_radius * pickup_radius
        for pickup in obs["pickups"]:
            kind = pickup["kind"]
            preference = counter_priority[kind]
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

        drop_goal = self._drop_goal(obs, me, target_dist)
        cube_goal = self._cube_goal(obs, me, target_dist)
        attacked_now = incoming is not None
        drop_held = drop_goal is not None and drop_goal[0]["holder"] == me["id"]
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

        movement_scale = 1.0
        resource_motion = False
        if not urgent_zone and not attacked_now and target_dist > 65:
            if drop_goal is not None:
                drop, drop_distance = drop_goal
                if drop_distance > max(0.0, drop["radius"] - 1.0):
                    mx, my = unit(drop["pos"][0] - x, drop["pos"][1] - y)
                else:
                    mx, my = 0.0, 0.0
                    movement_scale = 0.0
                resource_motion = True
            elif cube_goal is not None:
                cube, cube_distance = cube_goal
                mx, my = unit(cube["pos"][0] - x, cube["pos"][1] - y)
                movement_scale = 1.0 if cube_distance > 2.5 else 0.0
                resource_motion = True

        if (drop_goal is None and cube_goal is None and loot_target is not None
                and (target is None or target_dist > 100) and not urgent_zone):
            loot_distance, pickup = loot_target
            toward_loot = unit(pickup["pos"][0] - x, pickup["pos"][1] - y)
            weight = 1.1 * (1.0 - loot_distance / 90.0)
            mx += weight * toward_loot[0]
            my += weight * toward_loot[1]
        mx, my = unit(mx, my)
        if (not resource_motion and not urgent_zone and not attacked_now
                and target_dist > 100):
            walk_speed = max(1.0, me["stats"].get("walk_speed", 11.7))
            movement_scale = min(1.0, 4.6 / walk_speed)
        action["move"] = [mx * movement_scale, my * movement_scale]

        energy = me["energy"]
        action["boost"] = bool(
            energy > 45 and (
                urgent_zone
                or (target is not None and target_dist < 55)
                or (drop_goal is not None and not drop_held
                    and drop_goal[1] > 55 and drop_goal[0]["lands_in"] < 2)
            )
        )
        action["jump"] = bool(me["grounded"] and target is not None
                              and target_dist < 35 and obs["tick"] % 20 == 0)

        # Face the attacker that just damaged us before choosing a distant target.
        scan_yaw = (obs["t"] * 1.6 + self.slot * (2 * math.pi / 16) + math.pi) % (2 * math.pi) - math.pi
        aim_yaw = threat_yaw if threat_yaw is not None else scan_yaw
        desired_pitch = 0.0
        focus_incoming = incoming is not None and (
            target is None or attacker_id is None or target["id"] != attacker_id
        )
        aim_mode = None
        mortar_pitch = None
        laser = next((w for w in weapons if w and w["kind"] == "laser"), None)
        mortar = next((w for w in weapons if w and w["kind"] == "mortar"), None)
        lightning = next((w for w in weapons if w and w["kind"] == "lightning"), None)
        gun = next((w for w in weapons if w and w["kind"] == "gun"), None)
        if target is not None and not focus_incoming:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target["vel"]
            horizontal = max(1.0, math.hypot(tx - x, ty - y))
            muzzle_height = me["stats"].get("muzzle_height", 7.0)
            if laser is not None and target_dist <= laser["stats"].get("range", 280.0):
                aim_mode = "laser"
                flight = max(
                    0.0,
                    laser["stats"].get("charge", 0.75) - laser.get("charge", 0.0),
                )
            elif mortar is not None:
                mortar_stats = mortar["stats"]
                speed = mortar_stats.get("speed", 72.0)
                gravity = mortar_stats.get("gravity", 22.0)
                max_range = speed * speed / max(1.0, gravity)
                if 60.0 < target_dist < max_range * 0.9:
                    aim_mode = "mortar"
                    flight = 0.0
                elif gun is not None:
                    aim_mode = "gun"
                    flight = horizontal / max(1.0, gun["stats"]["speed"])
                else:
                    flight = 0.0
            elif gun is not None:
                aim_mode = "gun"
                flight = horizontal / max(1.0, gun["stats"]["speed"])
            elif lightning is not None:
                aim_mode = "lightning"
                flight = 0.0
            else:
                flight = 0.0
            px = tx + vx * flight - x
            py = ty + vy * flight - y
            if aim_mode == "mortar":
                muzzle_z = z + muzzle_height + 2.5
                target_z = tz + target_height
                for _ in range(2):
                    horizontal = max(1.0, math.hypot(px, py))
                    height = target_z - muzzle_z
                    discriminant = speed ** 4 - gravity * (
                        gravity * horizontal ** 2 + 2.0 * height * speed ** 2
                    )
                    if discriminant < 0:
                        mortar_pitch = None
                        break
                    tangent = (speed ** 2 - math.sqrt(discriminant)) / (gravity * horizontal)
                    mortar_pitch = math.atan(tangent)
                    flight = horizontal / max(1.0, speed * math.cos(mortar_pitch))
                    px = tx + vx * flight - x
                    py = ty + vy * flight - y
                    target_z = tz + target_height + vz * flight
                if mortar_pitch is not None:
                    desired_pitch = mortar_pitch
            else:
                pz = tz + target_height + vz * flight - (z + muzzle_height)
                desired_pitch = math.atan2(pz, max(1.0, math.hypot(px, py)))
            aim_yaw = math.atan2(py, px)
        action["aim"] = [aim_yaw, desired_pitch]

        fire = [False, False]
        max_hp = max(1.0, me["max_hp"])
        regen_mode = me["hp"] < max_hp * 0.68 and target_dist > 65 and not attacked_now
        can_attack = target is not None and not regen_mode and not focus_incoming
        if target is not None and target["kind"] == "npc":
            can_attack = can_attack and (
                attacker_id == target["id"]
                or (me["hp"] >= max_hp * 0.8 and not any(
                    actor["kind"] == "mech"
                    and math.dist(actor["pos"][:2], me["pos"][:2]) < 120
                    for actor in obs["mechs"]
                ))
            )
        target_yaw = math.atan2(target["pos"][1] - y, target["pos"][0] - x) if target else 0.0
        aligned_yaw = abs(angle_delta(me["aim"][0], aim_yaw)) <= 0.12
        aligned_pitch = abs(me["aim"][1] - desired_pitch) <= 0.12
        if target is not None:
            muzzle = [x, y, z + me["stats"].get("muzzle_height", 7.0)]
            target_center = [
                target["pos"][0], target["pos"][1], target["pos"][2] + target_height,
            ]
            line_blocked = self._line_blocked(muzzle, target_center)
        else:
            line_blocked = True

        if len(weapons) > 0 and weapons[0]:
            weapon = weapons[0]
            if weapon["kind"] == "gun":
                ammo = weapon.get("ammo", 0)
                if ammo <= 2 and weapon.get("reload", 0) <= 0:
                    action["reload"] = True
                elif (can_attack and target_dist <= weapon["stats"].get("range", 230.0)
                      and not line_blocked and aim_mode != "mortar"):
                    fire[0] = True
            elif weapon["kind"] == "laser" and can_attack:
                charge = weapon.get("charge", 0.0)
                full_charge = weapon["stats"].get("charge", 0.75)
                if target_dist <= weapon["stats"].get("range", 280.0) and not line_blocked:
                    fire[0] = not (
                        charge >= full_charge - 0.02 and aligned_yaw and aligned_pitch
                    )
            elif weapon["kind"] == "lightning" and can_attack:
                cone = math.radians(weapon["stats"].get("cone_deg", 30.0) * 0.5)
                if (target_dist <= weapon["stats"].get("range", 45.0)
                        and aligned_yaw and abs(angle_delta(me["aim"][0], target_yaw)) <= cone
                        and not line_blocked
                        and me["energy"] >= weapon["stats"].get("energy", 3.0)):
                    fire[0] = True
            elif weapon["kind"] == "mortar" and can_attack:
                if (aim_mode == "mortar" and mortar_pitch is not None
                        and target_dist > weapon["stats"].get("splash_radius", 7.0) * 2
                        and abs(angle_delta(me["aim"][0], aim_yaw)) < 0.18
                        and aligned_pitch):
                    fire[0] = True

        if len(weapons) > 1 and weapons[1]:
            weapon = weapons[1]
            if weapon["kind"] == "gun":
                ammo = weapon.get("ammo", 0)
                if ammo <= 2 and weapon.get("reload", 0) <= 0:
                    action["reload"] = True
                elif (can_attack and target_dist <= weapon["stats"].get("range", 230.0)
                      and not line_blocked and aim_mode != "mortar"):
                    fire[1] = True
            elif weapon["kind"] == "laser":
                charge = weapon.get("charge", 0.0)
                full_charge = weapon["stats"].get("charge", 0.75)
                if can_attack and target_dist <= weapon["stats"].get("range", 280.0) and not line_blocked:
                    fire[1] = not (
                        charge >= full_charge - 0.02 and aligned_yaw and aligned_pitch
                    )
            elif weapon["kind"] == "lightning" and can_attack:
                cone = math.radians(weapon["stats"].get("cone_deg", 30.0) * 0.5)
                if (target_dist <= weapon["stats"].get("range", 45.0)
                        and aligned_yaw and abs(angle_delta(me["aim"][0], target_yaw)) <= cone
                        and not line_blocked
                        and me["energy"] >= weapon["stats"].get("energy", 3.0)):
                    fire[1] = True
            elif weapon["kind"] == "mortar" and can_attack:
                if (aim_mode == "mortar" and mortar_pitch is not None
                        and target_dist > weapon["stats"].get("splash_radius", 7.0) * 2
                        and abs(angle_delta(me["aim"][0], aim_yaw)) < 0.18
                        and aligned_pitch):
                    fire[1] = True

        if any(fire):
            action["fire"] = fire
        elif len(weapons) > 1:
            action["fire"] = fire

        # Slot 1 is empty at spawn. Prefer laser for ranged fights, then
        # lightning; avoid mortar because self-splash and laser cook-off are
        # especially punishing in a survival-scored match.
        if (pickup_choice is not None
                and not (slot1 and slot1["kind"] == "laser" and slot1.get("charge", 0) > 0)):
            action["take"] = pickup_choice["id"]
            if slot1:
                action["drop"] = 1

        return action


mf.run(Bot)
