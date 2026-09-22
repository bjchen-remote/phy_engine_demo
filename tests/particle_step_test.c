/* Exercise the persistent borrowed-buffer ABI without Python validation. */
#include "../physics_demo/core/native/physics_native.h"
#include <assert.h>
#include <math.h>
#include <string.h>

int main(void) {
    int32_t material[1] = {PHY_MATERIAL_WATER};
    double x[1] = {0}, y[1] = {0}, z[1] = {0}, vx[1] = {.1}, vy[1] = {0}, vz[1] = {0};
    double ax[1] = {0}, ay[1] = {0}, az[1] = {0};
    double viscosity[1] = {0}, tension[1] = {0}, friction[1] = {0}, cohesion[1] = {0}, wetness[1] = {0};
    double frame_times[2] = {-9, -9};
    PhySimulation s = {.abi_version=PHY_ABI_VERSION, .particle_count=1, .frame_capacity=2,
        .density_iterations=2, .divergence_iterations=2, .thread_count=2, .max_substeps=1,
        .spacing=.1, .dt=.001, .duration=.02, .output_fps=10, .deadline_seconds=10,
        .bounds_min={-10,-10,-10}, .bounds_max={10,10,10},
        .material=material, .x=x, .y=y, .z=z, .vx=vx, .vy=vy, .vz=vz,
        .anchor_x=ax, .anchor_y=ay, .anchor_z=az, .viscosity=viscosity,
        .surface_tension=tension, .friction=friction, .cohesion=cohesion, .wetness=wetness,
        .frame_times=frame_times};
    PhyDiagnostics d;
    assert(!phy_context_create(NULL, &d) && d.status==PHY_STATUS_INVALID_ARGUMENT);
    assert(!phy_context_create(&s, NULL));
    assert(phy_context_step(NULL, 0, .001)==PHY_STATUS_INVALID_ARGUMENT);
    phy_context_destroy(NULL);
    for (int iteration=0; iteration<4; ++iteration) {
        PhyContext *context=phy_context_create(&s, &d);
        assert(context && d.status==PHY_STATUS_OK);
        assert(phy_context_step(context, 0, .001)==PHY_STATUS_OK);
        x[0]=2; vx[0]=3;
        assert(phy_context_step(context, .001, .001)==PHY_STATUS_OK);
        assert(fabs(x[0]-2.003)<1e-12 && d.substeps==2);
        assert(d.frames_written==0 && d.observations_written==0 && !d.completed);
        assert(frame_times[0]==-9 && frame_times[1]==-9);
        double previous=x[0];
        assert(phy_context_step(context, .019, .002)==PHY_STATUS_INVALID_ARGUMENT);
        assert(x[0]==previous);
        assert(phy_context_step(context, NAN, .001)==PHY_STATUS_INVALID_ARGUMENT);
        assert(phy_context_step(context, 0, INFINITY)==PHY_STATUS_INVALID_ARGUMENT);
        x[0]=1e300;
        assert(phy_context_step(context, .002, .001)==PHY_STATUS_INTERNAL_ERROR);
        x[0]=NAN;
        assert(phy_context_step(context, .002, .001)==PHY_STATUS_NONFINITE);
        phy_context_destroy(context);
        x[0]=0;vx[0]=.1;
    }
    s.particle_count=INT32_MAX;
    assert(!phy_context_create(&s,&d) && d.status==PHY_STATUS_INVALID_ARGUMENT);
    s.particle_count=1;s.field_count=INT32_MAX;
    assert(!phy_context_create(&s,&d) && d.status==PHY_STATUS_INVALID_ARGUMENT);
    s.field_count=0;s.x=NULL;
    assert(!phy_context_create(&s,&d) && d.status==PHY_STATUS_INVALID_ARGUMENT);
    s.x=x;material[0]=17;
    assert(!phy_context_create(&s,&d) && d.status==PHY_STATUS_INVALID_ARGUMENT);
    material[0]=PHY_MATERIAL_WATER;wetness[0]=NAN;
    assert(!phy_context_create(&s,&d) && d.status==PHY_STATUS_INVALID_ARGUMENT);
    wetness[0]=0;s.deadline_seconds=0;
    assert(!phy_context_create(&s,&d) && d.status==PHY_STATUS_TIMED_OUT);
    return 0;
}
