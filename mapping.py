import numpy as np
import os

class OccupancyGrid:
    def __init__(self, size=200, resolution=5.0):
        self.size = size
        self.resolution = resolution # cm per grid cell
        self.grid = np.zeros((self.size, self.size), dtype=np.int8)
        self.origin = (self.size // 2, self.size // 2)
        self.map_file = "data/saved_map.npy"
        os.makedirs("data", exist_ok=True)

    def world_to_grid(self, x_cm, y_cm):
        gx = int(self.origin[0] + (x_cm / self.resolution))
        gy = int(self.origin[1] + (y_cm / self.resolution))
        return gx, gy

    def update_cell(self, x_cm, y_cm, status):
        gx, gy = self.world_to_grid(x_cm, y_cm)
        if 0 <= gx < self.size and 0 <= gy < self.size:
            self.grid[gx, gy] = status

    def save_map(self):
        np.save(self.map_file, self.grid)

    def load_map(self):
        if os.path.exists(self.map_file):
            try:
                self.grid = np.load(self.map_file)
                return True
            except Exception as e:
                print(f"Error loading map: {e}")
        return False

    def get_payload(self):
        return self.grid.tolist()
