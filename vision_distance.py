import cv2

class VisionSensor:
    def __init__(self, cam_height_cm=10.0, focal_length_px=350.0):
        self.cam_height = cam_height_cm
        self.focal_length = focal_length_px

    def find_lowest_obstacle_pixel(self, frame):
        if frame is None:
            return None
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            edges = cv2.Canny(blurred, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            if not contours:
                return None
            return max((cv2.boundingRect(c)[1] + cv2.boundingRect(c)[3]) for c in contours)
        except Exception as e:
            print(f"Error in find_lowest_obstacle_pixel: {e}")
            return None

    def estimate_distance(self, frame):
        if frame is None:
            return None
        lowest_y = self.find_lowest_obstacle_pixel(frame)
        if lowest_y is None:
            return None
            
        c_y = frame.shape[0] / 2.0
        if lowest_y <= c_y:
            return None
            
        return (self.cam_height * self.focal_length) / (lowest_y - c_y)
        
    def calibrate_focal_length(self, frame, target_dist_cm):
        if frame is None:
            return None
        lowest_y = self.find_lowest_obstacle_pixel(frame)
        if lowest_y is None:
            return None
            
        c_y = frame.shape[0] / 2.0
        if lowest_y <= c_y:
            return None
            
        computed_focal_length = (target_dist_cm * (lowest_y - c_y)) / self.cam_height
        self.focal_length = computed_focal_length
        return computed_focal_length
