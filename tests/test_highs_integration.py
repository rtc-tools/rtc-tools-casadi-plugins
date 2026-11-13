"""Integration tests for the rtctools_highs plugin.

Verifies that the HiGHS solver produces numerically correct solutions and that
the correct HiGHS version is loaded from the installed wheel.
"""
import re
import subprocess
import sys
import time

import casadi as ca
import pytest

import rtctools_highs


@pytest.fixture(scope="module")
def highs_solver():
    """Build the QP solver once per module — min (x-1)^2 s.t. 0 <= x <= 2."""
    x = ca.MX.sym("x")
    qp = {"x": x, "f": (x - 1) ** 2, "g": x}
    return ca.qpsol("integrity", "highs", qp, {"highs": {"output_flag": True}})


class TestPluginIntegrity:
    """Checks that the correct HiGHS version is loaded, produces an optimal
    solution, and completes in reasonable time."""

    def test_highs_version(self):
        # HiGHS prints its "Running HiGHS <version>" banner once per process,
        # so run the solve in a fresh interpreter to be independent of test order.
        code = (
            "import casadi as ca, rtctools_highs\n"
            "x = ca.MX.sym('x')\n"
            "s = ca.qpsol('v', 'highs', {'x': x, 'f': (x - 1) ** 2, 'g': x},"
            " {'highs': {'output_flag': True}})\n"
            "s(lbx=-10, ubx=10, lbg=0, ubg=2)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        match = re.search(r"Running HiGHS (\S+)", proc.stdout)
        assert match, f"HiGHS version line not found in output:\n{proc.stdout}"
        assert match.group(1) == rtctools_highs.__highs_version__

    def test_optimality(self, highs_solver):
        sol = highs_solver(lbx=-10, ubx=10, lbg=0, ubg=2)

        assert float(sol["x"]) == pytest.approx(1.0, abs=1e-6)
        assert float(sol["f"]) == pytest.approx(0.0, abs=1e-10)

    def test_solve_time(self, highs_solver):
        t0 = time.monotonic()
        highs_solver(lbx=-10, ubx=10, lbg=0, ubg=2)
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0


class TestLinearProgram:
    """min  -x - 2y  s.t.  x + y <= 4,  x <= 3,  y <= 3,  x,y >= 0
    Optimal: x=1, y=3, f=-7"""

    def test_lp(self):
        x = ca.MX.sym("x")
        y = ca.MX.sym("y")
        xy = ca.vertcat(x, y)
        g = ca.vertcat(x + y, x, y)
        qp = {"x": xy, "f": -x - 2 * y, "g": g}
        solver = ca.qpsol("lp", "highs", qp)
        sol = solver(lbx=0, ubx=ca.inf, lbg=[-ca.inf, -ca.inf, -ca.inf], ubg=[4, 3, 3])

        assert float(sol["x"][0]) == pytest.approx(1.0, abs=1e-6)
        assert float(sol["x"][1]) == pytest.approx(3.0, abs=1e-6)
        assert float(sol["f"]) == pytest.approx(-7.0, abs=1e-6)


class TestQuadraticProgram:
    """min  (x-1)^2 + (y-2)^2  s.t.  x + y <= 3,  x,y >= 0
    Unconstrained optimum (1,2) is feasible (1+2=3), so optimal = (1,2), f=0"""

    def test_qp(self):
        x = ca.MX.sym("x")
        y = ca.MX.sym("y")
        xy = ca.vertcat(x, y)
        qp = {"x": xy, "f": (x - 1) ** 2 + (y - 2) ** 2, "g": x + y}
        solver = ca.qpsol("qp", "highs", qp)
        sol = solver(lbx=0, ubx=ca.inf, lbg=-ca.inf, ubg=3)

        assert float(sol["x"][0]) == pytest.approx(1.0, abs=1e-5)
        assert float(sol["x"][1]) == pytest.approx(2.0, abs=1e-5)
        assert float(sol["f"]) == pytest.approx(0.0, abs=1e-9)


class TestMixedIntegerLP:
    """min  -x - y  s.t.  x + y <= 3.5,  x,y in {0,1,2,...}
    Optimal: x+y=3, f=-3"""

    def test_milp(self):
        x = ca.MX.sym("x")
        y = ca.MX.sym("y")
        xy = ca.vertcat(x, y)
        qp = {"x": xy, "f": -x - y, "g": x + y}
        solver = ca.qpsol("milp", "highs", qp, {"discrete": [True, True]})
        sol = solver(lbx=0, ubx=10, lbg=-ca.inf, ubg=3.5)

        assert float(sol["x"][0]) + float(sol["x"][1]) == pytest.approx(3.0, abs=1e-6)
        assert float(sol["f"]) == pytest.approx(-3.0, abs=1e-6)
