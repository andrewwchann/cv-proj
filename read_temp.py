# Called by camera_server.py to read IMX219 sensor temperature register
# example command

# sudo i2ctransfer -f -y 9 w3@0x10 0x01 0x40 0x80
# sleep(0.2s)
# sudo i2ctransfer -f -y 9 w2@0x10 0x01 0x40 r1

# register = 0x0140 from the data sheet
# value = 0x80 to trigger a new temperature measurement
# value = r1 to read the result where the temperature is encoded in the lower 7 bits
import json
import subprocess
import time


IMX219_ADDR = "0x10"
TEMP_REG_HIGH = "0x01"
TEMP_REG_LOW = "0x40"


def run_i2ctransfer(args):
    result = subprocess.run(
        ["sudo", "i2ctransfer", "-f", "-y", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def trigger_imx219_temperature(bus):
    # Write 0x80 to register 0x0140.
    run_i2ctransfer([
        str(bus),
        f"w3@{IMX219_ADDR}",
        TEMP_REG_HIGH,
        TEMP_REG_LOW,
        "0x80",
    ])


def read_imx219_temperature_register(bus):
    # Read 1 byte from register 0x0140.
    output = run_i2ctransfer([
        str(bus),
        f"w2@{IMX219_ADDR}",
        TEMP_REG_HIGH,
        TEMP_REG_LOW,
        "r1",
    ])

    # Expected output like: "0xc1"
    return int(output.split()[0], 16)


def decode_imx219_temperature(reg_value):
    enabled = bool(reg_value & 0x80)
    raw = reg_value & 0x7F  
    temp_c = raw * 105.0 / 128.0 - 10.0

    return {
        "register": reg_value,
        "enabled": enabled,
        "raw": raw,
        "temp_c": temp_c,
    }


def sample_imx219_temperature(bus, delay_s=0.2):
    trigger_imx219_temperature(bus)
    time.sleep(delay_s)

    reg_value = read_imx219_temperature_register(bus)
    return decode_imx219_temperature(reg_value)


if __name__ == "__main__":
    temps = {}
    for bus in [9, 10]:
        try:
            temp = sample_imx219_temperature(bus)
            temps[str(bus)] = {
                "register": f"0x{temp['register']:02X}",
                "raw": temp["raw"],
                "temp_c": round(temp["temp_c"], 1),
            }
        except subprocess.CalledProcessError as e:
            temps[str(bus)] = {"error": str(e).strip()}
    print(json.dumps(temps))