import cv2
import numpy as np

capture = cv2.VideoCapture(0)

def rolling_shutter(frame):
    edges = cv2.Canny(frame, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 80, minLineLength=50, maxLineGap=10)
    return lines

# helper for sharpness
def laplacian_sharpness(frame):
    laplacian = cv2.Laplacian(frame, cv2.CV_64F)
    sharpness = laplacian.var()
    return sharpness

# see sharpness (focus) in each region of the gray frame
def regional_sharpness(frame):
    height, width = frame.shape
    row, col = 3, 3
    scores = []
    for i in range(row):
        for j in range(col):
            x_start = j * width // col
            x_end = (j + 1) * width // col
            y_start = i * height // row
            y_end = (i + 1) * height // row
            
            region = frame[y_start:y_end, x_start:x_end]
            score = laplacian_sharpness(region)
            scores.append(score)
            
    return scores

def display_sharpness(frame, regional_sharpness_scores):    
    height, width = frame.shape[0:2]
    rows, cols = 3, 3
    for i in range(rows):
        for j in range(cols):
            score_index = i * cols + j
            score = regional_sharpness_scores[score_index]
            x_start = j * width // cols
            y_start = i * height // rows
            text_x = x_start + 10
            text_y = y_start + 35
            cv2.putText(
                frame,
                f'S{score_index}: {score:.2f}',
                (text_x, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
            )
            

def contrast_score(frame):
    pass
            
while True:
    ret, frame = capture.read()
    if not ret:
        break

    # print("Frame shape:", frame.shape)
    # print("Frame data type:", frame.dtype)
    # # min is black (0), max is white (255) for uint8 images
    # print("min pixel value:", frame.min())
    # print("max pixel value:", frame.max())
    # print("mean pixel value:", frame.mean())

    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) # to gray scale
    display_frame = cv2.cvtColor(gray_frame, cv2.COLOR_GRAY2BGR)

    brightness = gray_frame.mean()
    contrast = gray_frame.std()
    regional_sharpness_scores = regional_sharpness(gray_frame)
    # mtf50 = estimate_mtf50(gray_frame)
    lines = rolling_shutter(gray_frame)


    # to display lines detected by rolling shutter method
    # if lines is not None:
    #     for line in lines:
    #         x1, y1, x2, y2 = line[0]
    #         cv2.line(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    
    
    display_sharpness(display_frame, regional_sharpness_scores)
    # cv2.putText(display_frame, f'Brightness: {brightness:.2f}', (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    # cv2.putText(display_frame, f'Contrast: {contrast:.2f}', (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)




    # cv2.imshow('Frame', frame)
    cv2.imshow('Gray Frame', display_frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

capture.release()
cv2.destroyAllWindows()
