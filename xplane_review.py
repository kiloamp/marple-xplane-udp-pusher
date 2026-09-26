"""Ten deterministic simulator review templates, selected from recorded evidence.

These are demo thresholds, not aircraft operating limits. Reviews cover the
recorded approach through first contact, not rollout, bounce or peak landing load.
ToLiss raw ILS values are deliberately excluded: their scale is unverified.
"""
import json
import math

REVIEW_VERSION = '1'
REVIEW_TEMPLATES = {
    'insufficient_data': 'There is too little usable final-approach telemetry to review this landing confidently.',
    'incomplete_landing': 'This recording ended before a confirmed touchdown; the approach can be explored, but the landing finish was not assessed.',
    'brisk_descent': 'Descent was still brisk just before first contact ({sink_fpm:.0f} ft/min downward). Focus the next attempt on reducing the descent rate before touchdown.',
    'bank_at_touchdown': 'The aircraft still had {touchdown_bank_deg:.1f} degrees of bank just before first contact. Aim for a more settled roll attitude at the finish.',
    'late_gear_command': 'The gear handle was still selected up during part of the approach below 500 ft above terrain. Try completing the landing setup earlier.',
    'speed_variation': 'Airspeed varied by about {speed_spread_kt:.0f} kt across the recorded final approach. Aim for more consistent speed through the descent.',
    'roll_corrections': 'The recorded final approach included substantial bank ({bank_p90_deg:.1f} degrees at the 90th percentile). Aim for smaller, smoother roll corrections.',
    'localizer_deviation': '{setup}the recorded ILS localizer was more than one dot from centre for {localizer_outside_pct:.0f}% of valid final-approach samples. Focus on keeping lateral tracking settled.',
    'glideslope_deviation': '{setup}the recorded ILS glideslope was more than one dot from centre for {glideslope_outside_pct:.0f}% of valid final-approach samples. Focus on keeping the vertical path settled.',
    'steady_approach': 'Good consistency in recorded speed and bank through final approach. None of the available measurements exceeded this simulator review’s thresholds.',
}


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered)-1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi]-ordered[lo]) * (index-lo)


def ils_channel(frequency):
    # Encoded hundredths of MHz: 108.10/108.15, 108.30/108.35, ...111.95.
    # FAA AIM ILS channel table; NAV1 display validity alone is not enough.
    if frequency is None or not math.isfinite(frequency):
        return False
    n = round(frequency)
    return abs(n-frequency) < .01 and 10810 <= n <= 11195 and n % 5 == 0 and (n//10) % 2 == 1


def review_capture(journal, metadata):
    latest = {}
    initial, final, before_contact = {}, {}, {}
    start = end = None
    last_sample = {}

    with journal.open() as file:
        for line in file:
            sample = json.loads(line)
            now = sample['time']
            if start is None:start = now
            end = now
            last_sample = sample
            for key, value in sample.items():
                if key != 'time' and isinstance(value, (float, int)) and math.isfinite(value):
                    latest[key] = (value, now)

            def fresh(key):
                value, at = latest.get(key, (None, -math.inf))
                return value if 0 <= now-at <= 1_000_000_000 else None

            def converted(primary, source, factor):
                value = fresh(primary)
                if value is not None:return value
                value = fresh(source)
                return value * factor if value is not None else None

            if fresh('paused') != 0 or fresh('replay') != 0 or fresh('on_ground') != 0:
                continue
            contact = any(key.startswith('gear_') and key.endswith('_compression_m') and value > .0001
                          for key, value in sample.items() if isinstance(value, (float, int)))
            if contact:continue
            frame = {'time': now, 'agl': converted('altitude_agl_ft','altitude_agl_m',1/.3048),
                     'speed': fresh('airspeed_kias'), 'bank': fresh('roll_deg'),
                     'vsi': converted('vertical_speed_fpm','vertical_speed_mps',60/.3048),
                     'gear': fresh('gear_handle_down')}
            if frame['bank'] is not None and frame['vsi'] is not None:
                before_contact = frame
            if any(frame[k] is None for k in ('agl','speed','bank','vsi')):
                continue
            bucket = (now-start)//1_000_000_000
            if now-start <= 20_000_000_000 and frame['agl'] > 1000:
                initial[bucket] = frame
            if not 100 <= frame['agl'] <= 1000:
                continue
            valid_ils = (ils_channel(fresh('nav1_frequency_raw'))
                         and fresh('nav1_horizontal_valid') == 1 and fresh('nav1_vertical_valid') == 1)
            for label, signal in [('loc','nav1_localizer_deviation_dots'),('gs','nav1_glideslope_deviation_dots')]:
                value = fresh(signal)
                frame[label] = value if valid_ils and value is not None and abs(value) <= 5 else None
            final[bucket] = frame  # One observation per second, independent of packet fragmentation/rate.

    frames = list(final.values())
    metrics = {'final_seconds_observed':len(frames), 'initial_seconds_observed':len(initial)}
    category = 'insufficient_data'
    touchdown = (metadata.get('Capture End') == 'first gear compression'
                 and any(k.startswith('gear_') and k.endswith('_compression_m') and v > .0001
                         for k,v in last_sample.items() if isinstance(v,(float,int))))
    if start is not None and end-start >= 10_000_000_000 and not touchdown:
        category = 'incomplete_landing'
    elif len(frames) >= 10 and touchdown and before_contact and end-before_contact['time'] <= 1_000_000_000:
        speeds = [f['speed'] for f in frames]
        metrics.update(speed_spread_kt=percentile(speeds,.9)-percentile(speeds,.1),
                       bank_p90_deg=percentile([abs(f['bank']) for f in frames],.9),
                       sink_fpm=max(0,-before_contact['vsi']),
                       touchdown_bank_deg=abs(before_contact['bank']))
        low_gear = [f['gear'] for f in frames if f['agl'] < 500 and f['gear'] in (0,1)]
        loc, gs = ([abs(f[k]) for f in frames if f[k] is not None] for k in ('loc','gs'))
        metrics['ils_seconds_observed'] = min(len(loc),len(gs))
        metrics['localizer_seconds_observed'] = len(loc)
        metrics['glideslope_seconds_observed'] = len(gs)
        metrics['localizer_outside_pct'] = 100*sum(v>1 for v in loc)/len(loc) if len(loc)>=10 else None
        metrics['glideslope_outside_pct'] = 100*sum(v>1 for v in gs)/len(gs) if len(gs)>=10 else None
        if metrics['sink_fpm'] > 600:category = 'brisk_descent'
        elif metrics['touchdown_bank_deg'] > 5:category = 'bank_at_touchdown'
        elif len(low_gear)>=5 and sum(v==0 for v in low_gear)/len(low_gear) > .2:category = 'late_gear_command'
        elif metrics['speed_spread_kt'] > 20:category = 'speed_variation'
        elif metrics['bank_p90_deg'] > 10:category = 'roll_corrections'
        elif metrics['localizer_outside_pct'] is not None and metrics['localizer_outside_pct'] > 20:category = 'localizer_deviation'
        elif metrics['glideslope_outside_pct'] is not None and metrics['glideslope_outside_pct'] > 20:category = 'glideslope_deviation'
        else:category = 'steady_approach'

    initial_frames = list(initial.values())
    settled_setup = (len(initial_frames)>=5
        and percentile([abs(f['bank']) for f in initial_frames],.9)<=8
        and percentile([f['speed'] for f in initial_frames],.9)-percentile([f['speed'] for f in initial_frames],.1)<=15)
    setup = 'Good initial speed and bank control, but ' if settled_setup else 'On this approach, '
    text = REVIEW_TEMPLATES[category].format(setup=setup, **metrics)
    if category not in {'insufficient_data','incomplete_landing'} and metrics.get('ils_seconds_observed',0)<10:
        missing = [label for label,key in [('localizer','localizer_seconds_observed'),('glideslope','glideslope_seconds_observed')]
                   if metrics.get(key,0)<10]
        label = 'ILS' if len(missing)==2 else missing[0].capitalize()
        text += f' {label} tracking was not assessed because valid standard NAV1 data was insufficient.'
    return {'version':REVIEW_VERSION,'category':category,'text':text,'metrics':metrics,
            'scope':'Recorded simulator approach through first contact; excludes rollout and bounce.'}
