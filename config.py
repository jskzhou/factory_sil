# Simulation timestep
SIM_DT = 0.05

# Timing constants (in seconds)
GATE_OPEN_TIME = 2.0
GATE_CLOSE_TIME = 2.0
CHAMBER_TRANSFER_TIME = 2.0
KILN_TRANSFER_TIME = 2.0

# Controller timeout limits per cycle phase (seconds)
TIMEOUT_LOADING       = 10.0
TIMEOUT_GATE_OPEN     = 5.0
TIMEOUT_GATE_CLOSE    = 5.0
TIMEOUT_TRANSFER_IN   = 5.0
TIMEOUT_TRANSFER_OUT  = 5.0
TIMEOUT_FLOODING      = 60.0

CHAMBER_VOLUME = 2.0  # m^3
MAX_CO2_FLOW = 0.25   # m^3/s
K_AIR = MAX_CO2_FLOW * 4.0  # approx of air flooding chamber rate, knife gate A >>> control valve
K_CO2 = MAX_CO2_FLOW / CHAMBER_VOLUME # CO2 displacement rate constant

AMBIENT_O2 = 21.0    # percentage
LOW_O2_TARGET = 2.0  # percentage