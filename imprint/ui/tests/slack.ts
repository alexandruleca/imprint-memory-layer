// How long a wait may take on the machine running the tests: CI runs on busy
// hosts and VMs, where a wait that is ample locally fails now and then.
// IMPRINT_TEST_TIME_SCALE if set, else 3 on CI, else 1, as tests/emulation.py
// and internal/testtime apply to the Python and Go tests.
const scale = Number(process.env.IMPRINT_TEST_TIME_SCALE) || (process.env.CI ? 3 : 1);

export const slack = (ms: number) => ms * scale;
