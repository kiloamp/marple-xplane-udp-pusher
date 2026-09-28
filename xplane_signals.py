"""X-Plane 11.55 DATA dictionary; preserve wire units and zero-based slot provenance.

Online meanings: Laminar's Data Set Output Table (10.30), checked against the
11.55 executable's output labels. Ambiguous aircraft-configured units stay explicit.
"""
import math
import struct

TABLE = 'https://www.x-plane.com/kb/data-set-output-table/'
REFS = 'https://developer.x-plane.com/datarefs/'
DATA = {}


def fields(group, title, specs):
    for slot, spec in enumerate(specs):
        if spec is None:
            continue
        name, unit, description = spec
        DATA[group, slot] = {
            'signal': 'data_' + name, 'unit': unit,
            'description': f'{description} Source: X-Plane 11 DATA group {group} ({title}), slot {slot}; {TABLE}',
            'metadata': {'protocol': 'DATA', 'group': str(group), 'slot': str(slot),
                         'source_url': TABLE, 'schema_version': 'X-Plane 11.55',
                         'unit_note': 'Wire value preserved; no conversion.'},
        }


def f(name, unit, description):
    return name, unit, description


fields(1, 'Times', [f('real_time_s','s','Elapsed real time since simulator launch.'),f('total_time_s','s','Simulator running time excluding loading.'),f('mission_time_s','s','Time since the mission was loaded.'),f('timer_s','s','General purpose elapsed timer.'),None,f('utc_time_h','h','Simulated UTC time of day in decimal hours.'),f('local_time_h','h','Simulated local time of day in decimal hours.'),f('hobbs_time_h','h','Aircraft Hobbs running time.')])
fields(3,'Speeds',[f('airspeed_indicated_kt','kt','Indicated airspeed.'),f('airspeed_equivalent_kt','kt','Equivalent airspeed.'),f('airspeed_true_kt','kt','True airspeed relative to air.'),f('groundspeed_kt','kt','Speed relative to the ground.'),None,f('airspeed_indicated_mph','mph','Indicated airspeed.'),f('airspeed_true_mph','mph','True airspeed relative to air.'),f('groundspeed_mph','mph','Speed relative to the ground.')])
fields(4,'Mach, vertical speed and loads',[f('mach','ratio','Airspeed divided by local speed of sound.'),None,f('vertical_speed_fpm','ft/min','Vertical speed, positive upwards.'),None,f('normal_load_g','g','Normal acceleration load in aircraft axes.'),f('axial_load_g','g','Longitudinal acceleration load in aircraft axes.'),f('side_load_g','g','Lateral acceleration load in aircraft axes.'),None])
fields(5,'Weather',[f('sea_level_pressure_inhg','inHg','Sea-level atmospheric pressure.'),f('sea_level_temperature_c','degC','Sea-level atmospheric temperature.'),None,f('wind_speed_kt','kt','Wind speed around the aircraft.'),f('wind_from_deg','deg','Direction wind comes from, clockwise from north.'),f('turbulence_local','ratio','Local turbulence setting.'),f('precipitation_local','ratio','Local precipitation setting.'),f('hail_local','ratio','Local hail setting.')])
fields(6,'Aircraft atmosphere',[f('ambient_pressure_inhg','inHg','Atmospheric pressure at the aircraft.'),f('ambient_temperature_c','degC','Ambient air temperature.'),f('leading_edge_temperature_c','degC','Leading-edge air temperature.'),f('density_ratio','ratio','Air-density ratio reported by the simulator.'),f('speed_of_sound_kt','kt','Local speed of sound.'),f('dynamic_pressure_psf','lbf/ft2','Aerodynamic dynamic pressure.'),None,f('gravity_fps2','ft/s2','Local gravitational acceleration.')])
fields(7,'System pressures',[f('barometer_inhg','inHg','Altimeter barometric setting.'),f('edens_part','ratio','X-Plane edens system-pressure field; physical interpretation undocumented in Laminar online table.'),*[f(f'{system}_{i}_ratio','ratio',f'{system.upper()} system {i} availability/pressure ratio; aircraft-dependent interpretation.') for system in ['vacuum','electrical','ahrs'] for i in [1,2]]])
for g,label in [(8,'joystick'),(10,'stability'),(11,'surface'),(138,'servo')]:
    specs=[f(f'{label}_{axis}_ratio','ratio',f'{label.capitalize()} {axis} input/deflection relative to full travel.') for axis in ['elevator','aileron','rudder']]
    if g==11: specs += [None,f('nosewheel_steering_deg','deg','Nosewheel steering angle from straight ahead.')]
    fields(g,label+' controls',specs)
fields(9,'Other controls',[*[f(f'{n}_request','ratio',f'Requested {n.replace("_"," ")} setting.') for n in ['thrust_vector','wing_sweep','wing_incidence','wing_dihedral','wing_retraction']],None,None,f('water_jettison','unspecified','Water jettison field; wire scale not specified by the online table.')])
fields(12,'Wing geometry',[*[f(f'wing_sweep_{n}_deg','deg',f'Wing sweep angle, simulator section {n}.') for n in ['1','2','horizontal']],*[f(f'{n}_ratio','ratio',f'Actual {n.replace("_"," ")} setting relative to full travel.') for n in ['thrust_vector','wing_sweep','wing_incidence','wing_dihedral','wing_retraction']]])
fields(13,'Trim and high-lift controls',[f(n,'ratio',d) for n,d in [('elevator_trim_ratio','Elevator trim setting.'),('aileron_trim_ratio','Aileron trim setting.'),('rudder_trim_ratio','Rudder trim setting.'),('flap_handle_ratio','Commanded flap handle position.'),('flap_position_ratio','Actual flap position.'),('slat_ratio','Actual slat position.'),('speedbrake_handle_ratio','Commanded speedbrake handle position.'),('speedbrake_position_ratio','Actual speedbrake position.')]])
fields(14,'Gear and brakes',[f('gear_handle_down','boolean','Landing gear extension command/status (0 up, 1 down).'),f('wheel_brake_set_ratio','ratio','Wheel brake set input.'),f('left_brake_added_ratio','ratio','Additional left toe-brake input.'),f('right_brake_added_ratio','ratio','Additional right toe-brake input.'),f('wheel_brake_ratio','ratio','Resulting wheel brake fraction.')])
fields(15,'Angular moments',[f(f'{a}_moment_ftlb','lbf*ft',f'Aircraft {a} moment ({l}).') for a,l in [('pitch','M'),('roll','L'),('yaw','N')]])
fields(16,'Angular velocities',[f(f'{a}_rate_radps','rad/s',f'Body-axis {a} angular velocity ({l}).') for a,l in [('pitch','Q'),('roll','P'),('yaw','R')]])
fields(17,'Attitude',[f(n,'deg',d) for n,d in [('pitch_deg','Pitch Euler angle.'),('roll_deg','Roll Euler angle.'),('heading_true_deg','True heading.'),('heading_magnetic_deg','Magnetic heading.')]])
fields(18,'Angles and paths',[f('angle_of_attack_deg','deg','Angle of attack alpha.'),f('sideslip_deg','deg','Aerodynamic sideslip beta.'),f('horizontal_path_deg','deg','Horizontal flight-path direction.'),f('vertical_path_deg','deg','Vertical flight-path angle.'),None,None,None,f('slip_indicator_deg','deg','Slip indicator angle reported by X-Plane.')])
fields(19,'Compass',[f('magnetic_compass_deg','deg','Magnetic compass indication.'),f('magnetic_variation_deg','deg','Local magnetic variation.')])
fields(20,'Position',[f('latitude_deg','deg','Aircraft geographic latitude.'),f('longitude_deg','deg','Aircraft geographic longitude.'),f('altitude_msl_ft','ft','Altitude above mean sea level.'),f('altitude_agl_ft','ft','Height above ground.'),f('on_runway','boolean','Simulator on-runway flag.'),f('altitude_indicated_ft','ft','Indicated altitude.'),f('origin_latitude_deg','deg','Latitude of the local coordinate origin.'),f('origin_longitude_deg','deg','Longitude of the local coordinate origin.')])
fields(21,'Local position and velocity',[*[f(f'local_{a}_m','m',f'Local Cartesian position {a.upper()}; origin can change as scenery loads.') for a in 'xyz'],*[f(f'local_v{a}_mps','m/s',f'Local Cartesian velocity {a.upper()}; X east, Y up, Z south near origin.') for a in 'xyz'],f('distance_ft','ft','X-Plane distance travelled field in feet.'),f('distance_nm','NM','X-Plane distance travelled field in nautical miles; keep separate from feet field.')])

# Eight wire slots are retained even when the aircraft has fewer components.
ENGINE = {
25:('throttle_command','ratio','Commanded throttle fraction'),26:('throttle_actual','ratio','Actual throttle fraction'),27:('mode','enum','Engine/propeller mode: 0 feather, 1 normal, 2 beta, 3 reverse'),28:('propeller_setting','ratio','Propeller control setting'),29:('mixture','ratio','Mixture control setting'),30:('carb_heat','ratio','Carburetor heat setting'),31:('cowl_flap','ratio','Cowl flap setting'),32:('magneto','enum','Magneto switch setting'),33:('starter_timeout_s','s','Starter timeout'),34:('power_hp','hp','Engine shaft power'),35:('thrust_lbf','lbf','Engine thrust'),36:('torque','aircraft-configured','Engine torque; X-Plane labels support lbf*ft or percent, verify aircraft configuration'),37:('rpm','rpm','Engine rotation speed'),38:('propeller_rpm','rpm','Propeller rotation speed'),39:('propeller_pitch_deg','deg','Propeller blade pitch'),40:('propwash_kt','kt','Propwash/jetwash speed'),41:('n1_pct','%','Low-pressure spool/fan N1 speed as percent reference'),42:('n2_pct','%','High-pressure spool/core N2 speed as percent reference'),45:('fuel_flow','aircraft-configured','Fuel flow; X-Plane labels support lb/h or gal/h, verify aircraft configuration'),47:('egt','aircraft-configured','Exhaust gas temperature indication; wire label is deg, temperature scale is aircraft-configured'),49:('oil_pressure_psi','psi','Engine oil pressure'),50:('oil_temperature','aircraft-configured','Oil temperature indication; wire label is deg, temperature scale is aircraft-configured'),56:('idle_high','boolean','Idle speed selector: low/high'),60:('fadec_on','boolean','Full-authority digital engine control enabled'),69:('propeller_efficiency','ratio','Propeller efficiency ratio'),78:('vertical_thrust_vector','unspecified','Total vertical thrust-vector field; online table does not specify its unit'),79:('lateral_thrust_vector','unspecified','Total lateral thrust-vector field; online table does not specify its unit'),80:('cyclic_pitch','unspecified','Rotor pitch cyclic disc tilt; online table does not specify its unit'),81:('cyclic_roll','unspecified','Rotor roll cyclic disc tilt; online table does not specify its unit')}
for g,(name,unit,desc) in ENGINE.items():
    fields(g,name,[f(f'engine_{i+1}_{name}',unit,f'{desc}, engine/propeller slot {i+1}. Unused slots may contain simulator defaults.') for i in range(8)])
for g,name,unit,desc in [(54,'voltage_v','V','Battery voltage'),(57,'on','boolean','Battery enabled')]:
    fields(g,'Battery',[f(f'battery_{i+1}_{name}',unit,f'{desc}, battery slot {i+1}; unused slots may contain defaults.') for i in range(8)])
fields(62,'Fuel quantities',[f(f'fuel_tank_{i+1}_quantity','aircraft-configured',f'Fuel quantity in tank slot {i+1}; X-Plane supports lb or gal labels, verify aircraft configuration.') for i in range(8)])
fields(63,'Mass and CG',[*[f(f'weight_{n}_lb','lb',f'Aircraft {n.replace("_"," ")} weight.') for n in ['empty','payload','fuel','jettisonable','current','maximum']],None,f('cg_from_reference_ft','ft','Center-of-gravity displacement behind the reference point.')])
for g,title,axes in [(64,'aerodynamic',['lift','drag','side']),(137,'gear',['normal','axial','side'])]:
    fields(g,title+' forces',[*[f(f'{title}_{a}_lbf','lbf',f'{title.capitalize()} {a} force.') for a in axes],*[f(f'{title}_{a}_moment_ftlb','lbf*ft',f'{title.capitalize()} {a} moment ({l}).') for a,l in [('roll','L'),('pitch','M'),('yaw','N')]]])
for g,name,unit,desc in [(66,'vertical_force_lbf','lbf','Landing gear vertical force'),(67,'deployment_ratio','ratio','Gear extension fraction, 0 retracted, 1 extended'),(134,'steering_deg','deg','Gear steering angle')]:
    fields(g,'Landing gear',[f(f'gear_{i}_{name}',unit,f'{desc}, zero-based gear slot {i}. Unused slots may contain defaults.') for i in range(8)])
fields(68,'Aerodynamic coefficients',[f('lift_drag_ratio','ratio','Total lift divided by drag.'),None,f('lift_coefficient','ratio','Total lift coefficient.'),f('drag_coefficient','ratio','Total drag coefficient.'),None,None,None,f('lift_drag_prop_efficiency','ratio','Lift-to-drag ratio multiplied by propeller efficiency.')])
for g,name in [(74,'elevator'),(75,'rudder')]:
    fields(g,name+' deflections',[f(f'{name}_slot_{i}_deflection_deg','deg',f'{name.capitalize()} deflection at DATA slot {i}; control set {i//2+1}. Physical surface depends on aircraft configuration.') for i in range(4)])
fields(76,'Yaw brakes',[f(f'yaw_brake_slot_{i}_deg','deg',f'Yaw-brake deflection, {"left" if i%2==0 else "right"}, wing pair {i//2+1}.') for i in range(8)])
fields(77,'Control forces',[f(f'control_force_{n}_lbf','lbf',f'Force on pilot {n.replace("_"," ")} control.') for n in ['pitch','roll','yaw','left_brake','right_brake']])
for g,name in [(92,'lift'),(93,'drag')]:
    fields(g,'Wing '+name,[f(f'wing_{i//2+1}_{"left" if i%2==0 else "right"}_{name}','unspecified',f'{name.capitalize()} from wing pair {i//2+1}, {"left" if i%2==0 else "right"}. Output label omits force unit; not assumed to match the force datarefs.') for i in range(8)])

BY_NAME = {entry['signal']: entry for entry in DATA.values()}


def decode_data(packet):
    if len(packet) < 5 or packet[:4] != b'DATA' or (len(packet)-5) % 36:
        raise ValueError('Not a complete DATA packet')
    values = {}
    for group,*slots in struct.iter_unpack('<i8f',packet[5:]):
        for slot,value in enumerate(slots):
            if not math.isfinite(value) or value == -999:
                continue
            entry = DATA.get((group,slot))
            if entry is None:
                # Never silently drop newly selected undocumented groups/slots.
                name = f'data_group_{group}_slot_{slot}'
                entry = {'signal': name, 'unit': 'unknown',
                         'description': f'Unmapped X-Plane DATA group {group}, slot {slot}. Raw value retained; meaning and unit require verification.',
                         'metadata': {'protocol':'DATA','group':str(group),'slot':str(slot),'mapping_status':'unverified'}}
                BY_NAME[name] = entry
            values[entry['signal']] = value
    return values


def signal_definitions(names, rrefs):
    from xplane_report_signals import EXTRA_DESCRIPTIONS, report_definition
    from xplane_approach import definition as approach_definition
    refs = {name:(ref,unit) for name,ref,unit in rrefs}
    descriptions = {
        'flight_time_s':'Elapsed simulator flight time.', 'paused':'Simulator pause flag: 1 paused, 0 running.',
        'replay':'Replay mode flag: 1 replay, 0 live simulation.', 'airspeed_kias':'Indicated airspeed in knots.',
        'true_airspeed_mps':'True airspeed relative to the surrounding air.', 'groundspeed_mps':'Speed relative to the ground.',
        'altitude_msl_m':'Altitude above mean sea level.', 'altitude_agl_m':'Height above local ground.',
        'latitude_deg':'Aircraft geographic latitude.', 'longitude_deg':'Aircraft geographic longitude.',
        'pitch_deg':'Aircraft pitch Euler angle.', 'roll_deg':'Aircraft roll Euler angle.',
        'heading_true_deg':'Aircraft heading relative to true north.', 'normal_g':'Normal acceleration load in aircraft axes.',
        'throttle_ratio':'Engine 1 throttle setting exposed by the flight model; not a measurement of thrust.', 'on_ground':'At least one landing gear touching the ground: 1 yes, 0 no.'}
    descriptions.update(EXTRA_DESCRIPTIONS)
    result=[]
    for name in sorted(names):
        special = report_definition(name) or approach_definition(name)
        if special is not None:
            result.append(special)
        elif name in BY_NAME:
            result.append(BY_NAME[name])
        elif name in refs:
            ref,unit=refs[name]
            desc=descriptions.get(name, f'Vertical tire/gear deflection at zero-based gear slot {name.split("_")[1]}; positive values indicate compression.' if name.startswith('gear_') else name)
            result.append({'signal':name,'unit':unit,'description':f'{desc} Dataref: {ref}. Source: {REFS}',
                           'metadata':{'protocol':'RREF','dataref':ref,'source_url':REFS}})
        else:
            raise ValueError(f'Missing signal description: {name}')
    return [{**{k:v for k,v in item.items() if k != "metadata"}, **item.get("metadata", {})} for item in result]
