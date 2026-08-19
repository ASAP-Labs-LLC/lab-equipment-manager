import os

def list_files(path):
    try:
        if not path:
            path = os.getcwd() # Default to current dir
        
        # Simple security check (expanded in real app)
        # if ".." in path: return {"error": "Invalid path"}

        if not os.path.exists(path):
             return {"error": "Path not found"}
        
        if not os.path.isdir(path):
             return {"error": "Not a directory"}

        items = []
        for name in os.listdir(path):
            full_path = os.path.join(path, name)
            is_dir = os.path.isdir(full_path)
            size = os.path.getsize(full_path) if not is_dir else 0
            items.append({
                "name": name,
                "path": full_path,
                "is_dir": is_dir,
                "size": size
            })
        
        # Sort: Directories first, then files
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return {"items": items, "current_path": path}

    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print(list_files("//asapserver/Labsharedrive/Ryan C/EQM/V4 - Beta"))
