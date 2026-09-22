/* Compile with ASan/UBSan against connections_native.c; no Python validation. */
#include "../physics_demo/core/native/connections_native.h"
#include <assert.h>
#include <math.h>
#include <string.h>

int main(void) {
    double x[6]={0,0,0,1.2,0,0},velocity[6]={0},mass[2]={1,1};
    int32_t fixed[2]={1,0};uint32_t mask[2]={0};
    Connection link={0,0,1,1,16,0};ConnectionMetric metric={5,0,0,0};
    double frames[12],times[2],observations[3],values[3];
    ConnectionSimulation s={.abi=CONNECTIONS_ABI,.nodes=2,.connections=1,.fields=0,.metrics=1,
        .frame_capacity=2,.observation_capacity=3,.iterations=8,.substeps=1,
        .dt=.01,.duration=.01,.fps=24,.deadline_seconds=1,
        .positions=x,.velocities=velocity,.mass=mass,.fixed=fixed,.field_mask=mask,
        .connection=&link,.field=0,.metric=&metric,.frames=frames,.frame_times=times,
        .observation_times=observations,.observation_values=values};
    ConnectionDiagnostics d;
    assert(connections_simulate(0,&d)==1);
    assert(connections_simulate(&s,0)==1);
    assert(connections_simulate(&s,&d)==0 && d.completed && d.frames_written==2 && d.observations_written==2);
    x[3]=1.2;memset(velocity,0,sizeof(velocity));
    s.deadline_seconds=0;assert(connections_simulate(&s,&d)==3 && !d.completed && d.frames_written==1);
    s.deadline_seconds=1;
    link.a=2;assert(connections_simulate(&s,&d)==1);link.a=0;
    link.b=-1;assert(connections_simulate(&s,&d)==1);link.b=1;
    metric.a=256;assert(connections_simulate(&s,&d)==1);metric.a=0;
    metric.type=7;assert(connections_simulate(&s,&d)==1);metric.type=5;
    link.type=1;link.stiffness=0;assert(connections_simulate(&s,&d)==1);link.type=0;link.stiffness=16;
    s.frame_capacity=1;assert(connections_simulate(&s,&d)==1);s.frame_capacity=2;
    s.observation_capacity=1;assert(connections_simulate(&s,&d)==1);s.observation_capacity=3;
    s.nodes=65;assert(connections_simulate(&s,&d)==1);s.nodes=2;
    s.connections=257;assert(connections_simulate(&s,&d)==1);s.connections=1;
    s.positions=0;assert(connections_simulate(&s,&d)==1);s.positions=x;
    x[3]=NAN;assert(connections_simulate(&s,&d)==1);x[3]=1.2;
    mass[1]=0;assert(connections_simulate(&s,&d)==1);mass[1]=1;
    fixed[0]=2;assert(connections_simulate(&s,&d)==1);fixed[0]=1;
    velocity[0]=1;assert(connections_simulate(&s,&d)==1);velocity[0]=0;
    link.damping=1e5;assert(connections_simulate(&s,&d)==1);link.damping=0;
    return 0;
}
