"""Short pixel samples on the owned test display, before settled-frame checks."""
import ctypes as c
import time


class Attributes(c.Structure):
    _fields_ = [(n, c.c_int) for n in ('x', 'y', 'width', 'height', 'border_width', 'depth')] + [
        ('visual', c.c_void_p), ('root', c.c_ulong), ('klass', c.c_int),
        ('bit_gravity', c.c_int), ('win_gravity', c.c_int), ('backing_store', c.c_int),
        ('backing_planes', c.c_ulong), ('backing_pixel', c.c_ulong), ('save_under', c.c_int),
        ('colormap', c.c_ulong), ('map_installed', c.c_int), ('map_state', c.c_int),
        ('all_event_masks', c.c_long), ('your_event_mask', c.c_long),
        ('do_not_propagate_mask', c.c_long), ('override_redirect', c.c_int), ('screen', c.c_void_p)]


class Image(c.Structure):
    _fields_ = [('width', c.c_int), ('height', c.c_int), ('xoffset', c.c_int),
        ('format', c.c_int), ('data', c.c_void_p), ('byte_order', c.c_int),
        ('bitmap_unit', c.c_int), ('bitmap_bit_order', c.c_int), ('bitmap_pad', c.c_int),
        ('depth', c.c_int), ('bytes_per_line', c.c_int), ('bits_per_pixel', c.c_int),
        ('red_mask', c.c_ulong), ('green_mask', c.c_ulong), ('blue_mask', c.c_ulong)]


def sample_shutdown(capture, xid, stop, guard):
    """Observe only the private test window's geometry/opacity during quit."""
    guard()
    x, d = capture.x, capture.display
    handler_type = c.CFUNCTYPE(c.c_int, c.c_void_p, c.c_void_p)
    ignore_destroyed = handler_type(lambda *_: 0)
    x.XSetErrorHandler.argtypes = [c.c_void_p]; x.XSetErrorHandler.restype = c.c_void_p
    x.XGetWindowAttributes.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(Attributes)]
    previous = x.XSetErrorHandler(c.cast(ignore_destroyed, c.c_void_p))
    samples = []
    start = time.monotonic()
    opacity = x.XInternAtom(d, b'_NET_WM_WINDOW_OPACITY', 0)
    try:
        attrs = Attributes()
        if not x.XGetWindowAttributes(d, xid, c.byref(attrs)):
            raise RuntimeError('Private shutdown window missing before stop')
        size = [attrs.width, attrs.height]
        stop()
        while time.monotonic() - start < 12:
            attrs = Attributes()
            if not x.XGetWindowAttributes(d, xid, c.byref(attrs)):
                samples.append({'ms': round((time.monotonic()-start)*1000, 2), 'gone': True})
                break
            entry = {'ms': round((time.monotonic()-start)*1000, 2),
                     'mapped': attrs.map_state == 2, 'size': [attrs.width, attrs.height]}
            actual, fmt, count, after, data = c.c_ulong(), c.c_int(), c.c_ulong(), c.c_ulong(), c.c_void_p()
            x.XGetWindowProperty(d, xid, opacity, 0, 1, 0, 0, c.byref(actual),
                                c.byref(fmt), c.byref(count), c.byref(after), c.byref(data))
            try:
                entry['opacity'] = (c.cast(data, c.POINTER(c.c_ulong))[0] / 0xffffffff
                                    if count.value and fmt.value == 32 else 1.0)
            finally:
                x.XFree(data)
            samples.append(entry)
            time.sleep(.01)
    finally:
        x.XSync(d, 0)
        x.XSetErrorHandler(previous)
    return {'initial_size': size, 'samples': samples,
            'visible_size_changes': sum(s.get('mapped', False) and s.get('opacity', 0) > .01
                                        and s['size'] != size for s in samples),
            'scope': 'Private X geometry/opacity sampling; not physical scanout or compositor animation'}


def sample_restore(capture, xid, reference_path, guard):
    guard()
    header = reference_path.read_bytes().split(b'\n', 3)
    width, height = map(int, header[1].split())
    reference = header[3]
    # Sample actual nonblack UI content, never classify a black background as
    # a complete frame. Reference images are downsampled by two by the caller.
    marked = [i for i in range(0, len(reference), 3) if max(reference[i:i+3]) > 32][::4]
    x, d = capture.x, capture.display
    x.XGetWindowAttributes.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(Attributes)]
    opacity_atom = x.XInternAtom(d, b'_NET_WM_WINDOW_OPACITY', 0)
    samples = []
    start = time.monotonic()
    while time.monotonic()-start < .75:
        attrs = Attributes()
        if not x.XGetWindowAttributes(d, xid, c.byref(attrs)):
            raise RuntimeError('Private restore window disappeared')
        entry = {'ms': round((time.monotonic()-start)*1000, 2), 'mapped': attrs.map_state == 2}
        actual, fmt, count, after, data = c.c_ulong(), c.c_int(), c.c_ulong(), c.c_ulong(), c.c_void_p()
        x.XGetWindowProperty(d, xid, opacity_atom, 0, 1, 0, 0, c.byref(actual),
                            c.byref(fmt), c.byref(count), c.byref(after), c.byref(data))
        try:
            entry['opacity'] = (c.cast(data, c.POINTER(c.c_ulong))[0] / 0xffffffff
                                if count.value and fmt.value == 32 else 1.0)
        finally:
            x.XFree(data)
        if entry['mapped']:
            ptr = x.XGetImage(d, xid, 0, 0, attrs.width, attrs.height, c.c_ulong(-1).value, 2)
            if not ptr: raise RuntimeError('Private restore image unavailable')
            try:
                image = c.cast(ptr, c.POINTER(Image)).contents
                if image.bits_per_pixel != 32 or image.byte_order != 0 or image.red_mask != 0xff0000:
                    raise RuntimeError('Unsupported private timing image format')
                raw = c.string_at(image.data, image.bytes_per_line * image.height)
                matches = 0
                for i in marked:
                    xx, yy = (i//3 % width)*2, (i//3//width)*2
                    if xx >= image.width or yy >= image.height: continue
                    offset = yy*image.bytes_per_line + xx*4
                    if max(abs(raw[offset+2-j]-reference[i+j]) for j in range(3)) < 32:
                        matches += 1
                entry['reference_match_fraction'] = matches/max(1, len(marked))
            finally:
                x.XDestroyImage(ptr)
        samples.append(entry)
        time.sleep(.008)
    return {'samples': samples, 'first_complete_ms': next((s['ms'] for s in samples
        if s.get('reference_match_fraction', 0) > .9), None),
        'first_presented_complete_ms': next((s['ms'] for s in samples
            if s['opacity'] > .99 and s.get('reference_match_fraction', 0) > .9), None),
        'visible_incomplete_samples': sum(s['mapped'] and s['opacity'] > .01 and
            s.get('reference_match_fraction', 0) <= .9 for s in samples),
        'scope': 'X drawable and requested opacity samples, not physical scanout'}
