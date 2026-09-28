"""Fixed LEPA 06L plot references; geometry, not a model of radio reception.

Coordinates: installed X-Plane 11.55 apt.dat / NAV1150 cycle 1802.
NAV1150 field definitions: https://developer.x-plane.com/wp-content/uploads/2020/03/XP-NAV1150-Spec.pdf
The configurable corridor is an illustrative training reference, not a pass/fail limit.
"""
import math
from dataclasses import dataclass

SOURCE = 'https://developer.x-plane.com/wp-content/uploads/2020/03/XP-NAV1150-Spec.pdf'


@dataclass(frozen=True)
class ApproachReference:
    threshold_lat: float = 39.5471472
    threshold_lon: float = 2.7107278
    localizer_lat: float = 39.563944444
    localizer_lon: float = 2.746277778
    course_deg: float = 58.483
    gs_lat: float = 39.549916667
    gs_lon: float = 2.713222222
    gs_elevation_m: float = 9.144
    glide_angle_deg: float = 3.0
    lateral_half_width_at_threshold_m: float = 105.0
    vertical_half_angle_deg: float = .35
    max_distance_m: float = 25000.0

    def scales(self):
        # WGS84 local chart projection. Fixed scales keep reference lines fixed
        # as the pilot deviates. Never centre the corridor on the flown track.
        lat = math.radians(self.threshold_lat)
        a, e2 = 6378137.0, 6.69437999014e-3
        w = math.sqrt(1 - e2 * math.sin(lat)**2)
        return math.pi/180*a*math.cos(lat)/w, math.pi/180*a*(1-e2)/w**3

    def xy(self, lat, lon):
        sx, sy = self.scales()
        return (lon-self.threshold_lon)*sx, (lat-self.threshold_lat)*sy

    def signals(self, lat, lon, altitude):
        if not all(math.isfinite(v) for v in (lat, lon, altitude)):
            return {}
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return {}
        e, n = self.xy(lat, lon)
        theta = math.radians(self.course_deg)
        along = e*math.sin(theta) + n*math.cos(theta)
        distance = -along
        cross = e*math.cos(theta) - n*math.sin(theta)
        # Include a short touchdown/runway tail but no back-course or distant airport.
        if not (-1200 <= distance <= self.max_distance_m and abs(cross) <= 5000):
            return {}
        le, ln = self.xy(self.localizer_lat, self.localizer_lon)
        loc_along = le*math.sin(theta) + ln*math.cos(theta)
        half_angle = math.atan(self.lateral_half_width_at_threshold_m/loc_along)
        sx, _ = self.scales()
        def longitude(angle):
            # Intersect each fixed beam ray with the AIRCRAFT latitude so Marple
            # can use one Y (latitude) for aircraft, centre and both boundaries.
            return self.threshold_lon + (le+(n-ln)*math.tan(angle))/sx
        ge, gn = self.xy(self.gs_lat, self.gs_lon)
        gs_distance = distance + ge*math.sin(theta) + gn*math.cos(theta)
        result = {
            'ils_plot_latitude_deg': lat, 'ils_plot_longitude_deg': lon,
            'ils_center_longitude_deg': longitude(theta),
            'ils_left_longitude_deg': longitude(theta+half_angle),
            'ils_right_longitude_deg': longitude(theta-half_angle),
        }
        # Stop the glidepath at the threshold: continuing below the runway is
        # misleading during flare/taxi. No fabricated zero-valued tail samples.
        if distance >= 0:
            center = self.gs_elevation_m + gs_distance*math.tan(math.radians(self.glide_angle_deg))
            result.update({
                'ils_distance_to_threshold_m': distance,
                'ils_aircraft_altitude_msl_m': altitude,
                'ils_glidepath_altitude_msl_m': center,
                'ils_lower_altitude_msl_m': self.gs_elevation_m + gs_distance*math.tan(math.radians(self.glide_angle_deg-self.vertical_half_angle_deg)),
                'ils_upper_altitude_msl_m': self.gs_elevation_m + gs_distance*math.tan(math.radians(self.glide_angle_deg+self.vertical_half_angle_deg)),
                'ils_lateral_error_m': (e-le)*math.cos(theta)-(n-ln)*math.sin(theta),
                'ils_vertical_error_m': altitude-center,
            })
        return result


LEPA_06L = ApproachReference()
APPROACH_METADATA = {
    'ILS Reference': 'LEPA 06L / PLM / 110.90 MHz / 3 deg; X-Plane NAV1150 cycle 1802',
    'ILS Corridor': 'Illustrative: +/-105 m lateral at threshold; +/-0.35 deg vertical; not measured receiver dots',
}
DEFINITIONS = {
    'ils_plot_latitude_deg': ('deg', 'Aircraft latitude shared as Y for the lateral scatter; paired fresh position sample.'),
    'ils_plot_longitude_deg': ('deg', 'Aircraft longitude X for lateral scatter, synchronized with ils_plot_latitude_deg.'),
    'ils_center_longitude_deg': ('deg', 'Fixed localizer centre ray longitude at the aircraft latitude. X with ils_plot_latitude_deg as Y.'),
    'ils_left_longitude_deg': ('deg', 'Illustrative left corridor longitude at aircraft latitude; narrows toward the localizer antenna.'),
    'ils_right_longitude_deg': ('deg', 'Illustrative right corridor longitude at aircraft latitude; narrows toward the localizer antenna.'),
    'ils_distance_to_threshold_m': ('m', 'Along-course distance remaining to the threshold, shared Y for all vertical scatter X signals. Not DME or distance flown.'),
    'ils_aircraft_altitude_msl_m': ('m', 'Aircraft geometric MSL altitude X, synchronized to ils_distance_to_threshold_m.'),
    'ils_glidepath_altitude_msl_m': ('m', 'Reference 3-degree glidepath MSL altitude X at the same remaining distance.'),
    'ils_lower_altitude_msl_m': ('m', 'Illustrative lower glidepath boundary (2.65 degrees) MSL altitude X.'),
    'ils_upper_altitude_msl_m': ('m', 'Illustrative upper glidepath boundary (3.35 degrees) MSL altitude X.'),
    'ils_lateral_error_m': ('m', 'Signed horizontal distance from localizer centreline; positive right when looking toward runway.'),
    'ils_vertical_error_m': ('m', 'Aircraft MSL altitude minus reference glidepath altitude; positive above.'),
}
PLOT_SIGNALS = frozenset(DEFINITIONS) - {'ils_lateral_error_m', 'ils_vertical_error_m'}


def definition(name):
    if name not in DEFINITIONS:return None
    unit, description = DEFINITIONS[name]
    return {'signal': name, 'unit': unit, 'description': description +
            f' Fixed LEPA 06L geometry in a local WGS84 chart projection; illustrative training corridor, not radio signal validity or scoring. Source: {SOURCE}',
            'protocol': 'derived', 'reference_runway': 'LEPA 06L', 'source_url': SOURCE}
