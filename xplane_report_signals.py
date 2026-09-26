"""Curated landing-report channels, in addition to the original 26 RREFs.

Standard references/units checked against X-Plane 11.55 Resources/plugins/DataRefs.txt.
ToLiss names checked against the installed A319 v1.11 plugin and a read-only UDP probe.
Custom ILS scaling is intentionally not inferred from its name or current value.
"""

STANDARD_SOURCE = 'https://developer.x-plane.com/datarefs/'
TOLISS_SOURCE = 'https://toliss.com/pages/support'

# name, dataref, unit, description
REPORT_RREFS = [
    ('vertical_speed_mps', 'sim/flightmodel/position/local_vy', 'm/s', 'True vertical velocity; positive climb, negative descent.'),
    ('vertical_speed_indicated_fpm', 'sim/cockpit2/gauges/indicators/vvi_fpm_pilot', 'ft/min', 'Pilot indicated vertical speed; may differ from true vertical velocity.'),
    ('altitude_indicated_ft', 'sim/cockpit2/gauges/indicators/altitude_ft_pilot', 'ft', 'Pilot barometric indicated altitude, affected by altimeter setting.'),
    ('heading_magnetic_deg', 'sim/flightmodel/position/mag_psi', 'deg', 'Aircraft magnetic heading.'),
    ('ground_track_true_deg', 'sim/flightmodel/position/hpath', 'deg', 'Direction of the ground track relative to true north.'),
    ('throttle_lever_1_ratio', 'sim/cockpit2/engine/actuators/throttle_ratio[0]', 'ratio', 'Engine 1 throttle lever request, 0 idle to 1 maximum normal; not delivered thrust.'),
    ('throttle_lever_2_ratio', 'sim/cockpit2/engine/actuators/throttle_ratio[1]', 'ratio', 'Engine 2 throttle lever request; ignore for a single-engine aircraft.'),
    ('engine_1_n1_pct', 'sim/cockpit2/engine/indicators/N1_percent[0]', '%', 'Engine 1 actual N1 speed, percent of reference; not requested thrust. Jet-specific.'),
    ('engine_2_n1_pct', 'sim/cockpit2/engine/indicators/N1_percent[1]', '%', 'Engine 2 actual N1 speed; ignore for a single-engine or non-jet aircraft.'),
    ('flap_handle_ratio', 'sim/cockpit2/controls/flap_ratio', 'ratio', 'Flap handle request, 0 retracted to 1 full; not a universal Airbus CONF code.'),
    ('flap_deploy_ratio', 'sim/flightmodel2/controls/flap_handle_deploy_ratio', 'ratio', 'Actual overall flap deployment, 0 retracted to 1 full, including deployment travel time.'),
    ('speedbrake_handle_ratio', 'sim/cockpit2/controls/speedbrake_ratio', 'ratio', 'Speedbrake handle request; -0.5 armed, 0 retracted, 1 full.'),
    ('speedbrake_deploy_ratio', 'sim/flightmodel2/controls/speedbrake_ratio', 'ratio', 'Actual speedbrake surface deployment, 0 retracted to 1 full.'),
    ('gear_handle_down', 'sim/cockpit2/controls/gear_handle_down', 'boolean', 'Landing gear handle request, 0 up and 1 down; not proof of gear lock.'),
    ('nav1_localizer_deviation_dots', 'sim/cockpit2/radios/indicators/nav1_hdef_dots_pilot', 'dot', 'Pilot NAV1 horizontal CDI deviation. Treat as localizer only when tuned to an ILS and horizontal guidance is valid.'),
    ('nav1_glideslope_deviation_dots', 'sim/cockpit2/radios/indicators/nav1_vdef_dots_pilot', 'dot', 'Pilot NAV1 vertical deviation. Interpret only with a valid vertical guidance indication.'),
    ('nav1_horizontal_valid', 'sim/cockpit2/radios/indicators/nav1_display_horizontal', 'boolean', 'NAV1 horizontal signal available; not specific to ILS. Custom aircraft may use their own indications.'),
    ('nav1_vertical_valid', 'sim/cockpit2/radios/indicators/nav1_display_vertical', 'boolean', 'NAV1 vertical signal available. Custom aircraft may use their own indications.'),
    ('flight_director_mode', 'sim/cockpit2/autopilot/flight_director_mode', 'enum', 'Standard flight director mode: 0 off, 1 on, 2 on with autopilot servos; custom aircraft may not mirror this.'),
    ('flight_director_pitch_deg', 'sim/cockpit2/autopilot/flight_director_pitch_deg', 'deg', 'Standard flight-director pitch deflection, positive up; aircraft-specific mirroring must be checked.'),
    ('flight_director_roll_deg', 'sim/cockpit2/autopilot/flight_director_roll_deg', 'deg', 'Standard flight-director roll deflection, positive right; aircraft-specific mirroring must be checked.'),
    ('wind_direction_true_deg', 'sim/weather/wind_direction_degt', 'deg', 'Effective wind direction at the aircraft, measured from true north.'),
    ('wind_speed_mps', 'sim/weather/wind_speed_kt', 'm/s', 'Effective wind speed at the aircraft. X-Plane 11 documents this scalar in m/s despite its misleading _kt name.'),
    ('aircraft_mass_kg', 'sim/flightmodel/weight/m_total', 'kg', 'Total aircraft mass, useful as landing report context.'),
    ('barometer_inhg', 'sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot', 'inHg', 'Pilot altimeter pressure setting.'),
    ('nav1_frequency_raw', 'sim/cockpit2/radios/actuators/nav1_frequency_hz', 'raw', 'X-Plane encoded NAV1 frequency integer; divide by 100 to obtain MHz, e.g. 11030 means 110.30 MHz.'),
]

TOLISS_RREFS = [
    ('toliss_fd1_engaged', 'AirbusFBW/FD1Engage', 'boolean', 'ToLiss captain flight director engagement indication.'),
    ('toliss_ap1_engaged', 'AirbusFBW/AP1Engage', 'boolean', 'ToLiss autopilot 1 engagement indication.'),
    ('toliss_ap2_engaged', 'AirbusFBW/AP2Engage', 'boolean', 'ToLiss autopilot 2 engagement indication.'),
    ('toliss_autothrust_mode_raw', 'AirbusFBW/ATHRmode', 'enum', 'ToLiss autothrust mode code; enum mapping has not been verified, retain raw code.'),
    ('toliss_ils1_localizer_raw', 'AirbusFBW/ILS1LocRaw', 'raw', 'ToLiss ILS receiver 1 raw localizer indication. Scale/sign and validity rules unverified; do not score as dots.'),
    ('toliss_ils1_glideslope_raw', 'AirbusFBW/ILS1GSRaw', 'raw', 'ToLiss ILS receiver 1 raw glideslope indication. Scale/sign and validity rules unverified; do not score as dots.'),
    ('toliss_localizer_display_flag', 'AirbusFBW/LOConCapt', 'raw', 'ToLiss captain localizer display flag; not independently verified as a signal-validity flag.'),
    ('toliss_glideslope_display_flag', 'AirbusFBW/GSonCapt', 'raw', 'ToLiss captain glideslope display flag; not independently verified as a signal-validity flag.'),
    ('toliss_ls_display_flag', 'AirbusFBW/ILSonCapt', 'raw', 'ToLiss captain landing-system display flag; display selection is not proof of valid ILS reception.'),
    ('toliss_flap_lever_ratio', 'AirbusFBW/FlapLeverRatio', 'ratio', 'ToLiss flap lever position; verify detent-to-CONF mapping before labeling configurations.'),
]

DERIVED = {
    'altitude_msl_ft': ('ft', 'Geometric MSL altitude converted from altitude_msl_m; not barometric indicated altitude.'),
    'altitude_agl_ft': ('ft', 'Height above local terrain converted from altitude_agl_m.'),
    'groundspeed_kt': ('kt', 'Ground speed converted from groundspeed_mps.'),
    'vertical_speed_fpm': ('ft/min', 'True vertical speed converted from vertical_speed_mps; positive climb, negative descent.'),
    'distance_covered_nm': ('NM', 'Estimated horizontal distance since this recording began, trapezoidal integration of groundspeed against simulator flight time. Pauses, replay, gaps over 2 s and backwards time are excluded; not runway distance or distance to threshold.'),
}
TOLISS_NAMES = {row[0] for row in TOLISS_RREFS}
EXTRA_DESCRIPTIONS = {name: desc for name, _, _, desc in REPORT_RREFS + TOLISS_RREFS}


def report_definition(name):
    if name in DERIVED:
        unit, desc = DERIVED[name]
        return {'signal': name, 'unit': unit, 'description': f'{desc} Sources: {STANDARD_SOURCE}',
                'protocol': 'derived', 'source_url': STANDARD_SOURCE}
    if name in TOLISS_NAMES:
        _, ref, unit, desc = next(row for row in TOLISS_RREFS if row[0] == name)
        return {'signal': name, 'unit': unit, 'description': f'{desc} Dataref: {ref}. Source: installed ToLiss A319 v1.11 plugin; {TOLISS_SOURCE}',
                'protocol': 'RREF', 'dataref': ref, 'source_url': TOLISS_SOURCE,
                'mapping_status': 'raw_scale_unverified' if unit in {'raw', 'enum'} else 'aircraft_specific'}
    return None
