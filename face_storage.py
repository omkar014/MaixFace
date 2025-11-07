import os, gc, ujson, struct, utime

FEATURE_DIM = 196
_storage_dir = None
_index_path = None
_index_data = []

# Init Path
def init(path="/sd/faces"):
    global _storage_dir, _index_path, _index_data
    _storage_dir = path.rstrip("/")
    _index_path = _storage_dir + "/index.json"

    try:
        sd_list = None
        try:
            sd_list = os.listdir("/sd")
        except Exception:
            sd_list = None

        if sd_list is not None:
            try:
                sub = _storage_dir.split("/", 2)[-1]
            except Exception:
                sub = "faces"
            if sub not in sd_list:
                try:
                    os.mkdir(_storage_dir)
                except Exception as e:
                    print("face_storage: mkdir failed:", e)
        else:
            try:
                os.mkdir(_storage_dir)
            except Exception as e:
                print("face_storage: mkdir attempt:", e)
    except Exception as e:
        print("face_storage: init outer error:", e)

    try:
        _index_data = _load_index()
    except Exception as e:
        print("face_storage: load index failed:", e)
        _index_data = []

# Load Index
def _load_index():
    try:
        try:
            files = os.listdir(_storage_dir)
        except Exception:
            files = None

        if files is not None:
            if "index.json" not in files:
                return []
        else:
            pass

        try:
            import ujson as json
        except Exception:
            try:
                import json
            except Exception:
                json = None

        try:
            os.stat(_index_path)
        except OSError:
            return []

        with open(_index_path, "r") as f:
            content = f.read()
            if not content:
                return []
            if json:
                try:
                    data = json.loads(content)
                    if isinstance(data, list):
                        return data
                    else:
                        return []
                except Exception as e:
                    print("face_storage: index json parse error:", e)
                    return []
            else:
                print("face_storage: json module not available")
                return []
    except Exception as e:
        print("face_storage: _load_index exception:", e)
        return []

# Save Index
def _save_index():
    try:
        try:
            import ujson as json
        except Exception:
            try:
                import json
            except Exception:
                json = None

        if json:
            content = json.dumps(_index_data)
        else:
            arr = []
            for it in _index_data:
                nm = str(it.get("name", "")).replace('"', '\\"')
                fn = str(it.get("file", ""))
                tm = it.get("time", 0)
                arr.append('{"name":"%s","file":"%s","time":%s}' % (nm, fn, str(tm)))
            content = "[" + ",".join(arr) + "]"

        tmp = _index_path + ".tmp"
        with open(tmp, "w") as f:
            f.write(content)
            try:
                f.flush()
            except Exception:
                pass
        try:
            os.rename(tmp, _index_path)
        except Exception:
            try:
                try:
                    os.stat(_index_path)
                    os.remove(_index_path)
                except OSError:
                    pass
                os.rename(tmp, _index_path)
            except Exception as e:
                print("face_storage: rename failed:", e)
                try:
                    with open(_index_path, "w") as f2:
                        f2.write(content)
                except Exception as ee:
                    print("face_storage: fallback write failed:", ee)
                    return False
        return True
    except Exception as e:
        print("face_storage: _save_index exception:", e)
        return False


# Read Faces
def _read_face_file(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 8 + 4 * FEATURE_DIM:
            print("face_storage: read failed: file too small")
            return None

        magic = data[:8]
        if not magic.startswith(b"FRv1"):
            print("face_storage: warning — invalid magic in", path)
            return None

        start = 8
        feature_data = data[start:start + 4 * FEATURE_DIM]
        feature = struct.unpack("f" * FEATURE_DIM, feature_data)
        pos = start + 4 * FEATURE_DIM

        if pos < len(data):
            name_len = data[pos]
            pos += 1
            name = data[pos:pos + name_len].decode("utf-8")
        else:
            name = "Unknown"

        gc.collect()
        return {"name": name, "feature": feature}

    except Exception as e:
        print("face_storage: read failed:", e)
        return None

# Write Faces
def _write_face_file(path, name, feature):
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(b"FRv1\0\0\0\0")
            f.write(struct.pack("f"*len(feature), *feature))
            name_b = name.encode("utf-8")
            f.write(struct.pack("B", len(name_b)))
            f.write(name_b)
        os.rename(tmp, path)
        return True
    except Exception as e:
        print("face_storage: write failed:", e)
        return False

# Save Faces
def save_new_face(feature):
    global _index_data
    MAX_FACE_COUNT = 10

    if not _storage_dir:
        init()

    used = set()
    for it in _index_data:
        fn = it.get("file")
        if fn and fn.startswith("face_") and fn.endswith(".bin"):
            try:
                num = int(fn[5:8])
                used.add(num)
            except:
                pass

    idx = 1
    while idx <= MAX_FACE_COUNT and idx in used:
        idx += 1

    if idx > MAX_FACE_COUNT:
        print("face_storage: Reached the maximum quantity (%d)" % MAX_FACE_COUNT)
        return None

    name = "Mr.%d" % idx
    fname = "face_%03d.bin" % idx
    path = _storage_dir + "/" + fname

    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(b"FRv1\0\0\0\0")
            feature = tuple(feature)
            f.write(struct.pack("f" * len(feature), *feature))
            name_b = name.encode("utf-8")
            f.write(struct.pack("B", len(name_b)))
            f.write(name_b)
        os.rename(tmp, path)
    except Exception as e:
        print("face_storage: Failed to save:", e)
        try:
            os.stat(tmp)
            os.remove(tmp)
        except OSError:
            pass
        return None

    entry = {
        "name": name,
        "file": fname,
        "time": utime.localtime()
    }
    replaced = False
    for it in _index_data:
        if it.get("file") == fname:
            it.update(entry)
            replaced = True
            break
    if not replaced:
        _index_data.append(entry)

    if not _save_index():
        print("face_storage: Warning — Failed to save index")

    gc.collect()
    print("face_storage: Success", name)
    return name

# Load Faces (Gradually)
def load_all(record_ftrs, names):
    global _index_data
    if not _storage_dir:
        init()
    loaded = 0
    for it in _index_data:
        fname = it.get("file")
        if not fname:
            continue
        path = _storage_dir + "/" + fname
        info = _read_face_file(path)
        if info:
            names.append(info["name"])
            record_ftrs.append(tuple(info["feature"]))
            loaded += 1
        gc.collect()
    return loaded

'''# Clear ALL Faces
def clear_all():
    global _index_data
    print("face_storage: clearing all faces...")
    try:
        for f in os.listdir(_storage_dir):
            if f.endswith(".bin"):
                os.remove(_storage_dir + "/" + f)
        try:
            os.stat(_index_path)
            os.remove(_index_path)
        except OSError:
            pass
        _index_data = []
        gc.collect()
        print("face_storage: cleared")
        return True
    except Exception as e:
        print("face_storage: clear failed:", e)
        return False
'''

# LUNGMEN ELECTRONICS 2025
