import pyvisa
import time

rm = pyvisa.ResourceManager()
smu = rm.open_resource('USB0::0x05E6::0x2450::04448984::INSTR')

smu.write('*RST')
smu.write(':SOUR:FUNC VOLT')
smu.write(':SOUR:VOLT:RANG 20')
smu.write(':SENS:CURR:PROT 0.001')
smu.write(':SOUR:VOLT:LEV 2')

print("Applying +2V for 5 seconds...")
smu.write(':OUTP ON')
time.sleep(5)
smu.write(':OUTP OFF')
print("Done! Voltage stopped.")

smu.close()
