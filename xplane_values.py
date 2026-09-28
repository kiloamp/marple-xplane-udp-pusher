"""Marple's long format separates numeric samples from real text samples."""


def value_columns(value):
    return {'value': None, 'value_text': value} if isinstance(value, str) else {'value': value, 'value_text': None}


def signal_row(timestamp, name, value):
    return {'time': timestamp, 'signal': name, **value_columns(value)}
