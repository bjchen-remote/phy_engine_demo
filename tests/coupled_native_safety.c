#include "../physics_demo/core/native/coupled_native.h"
#include <assert.h>
#include <math.h>
#include <string.h>

int main(void) {
    CoupledPoint point={.position={0,0,0},.velocity={1,0,0},.inverse_mass=1,.radius=0};
    CoupledRigid rigid={0};CoupledLink link={0};CoupledEntity entity={1,0,1};CoupledMetric metric={0};
    PhyForceField field={0};PhyCollider collider={0};
    double point_frames[6],rigid_frames[14],times[2],observation_times[12],values[12];
    CoupledSimulation s={.abi=COUPLED_ABI,.points=1,.entities=1,.substeps=2,.iterations=2,
        .frame_capacity=2,.dt=.001,.duration=.01,.fps=10,.deadline_seconds=10,
        .point=&point,.rigid=&rigid,.link=&link,.entity=&entity,.metric=&metric,.field=&field,.collider=&collider,
        .point_frames=point_frames,.rigid_frames=rigid_frames,.frame_times=times,
        .observation_times=observation_times,.observation_values=values};
    CoupledDiagnostics d;PhyDiagnostics pd;MeshDiagnostics md;
    #define RUN() coupled_simulate(&s,&d,&pd,&md)
    assert(coupled_simulate(NULL,&d,&pd,&md)==1);
    assert(coupled_simulate(&s,NULL,&pd,&md)==1);
    assert(RUN()==0 && d.completed && d.frames_written==2);
    assert(fabs(point.position.x-.01)<1e-12);
    s.points=65;assert(RUN()==1);s.points=1;
    s.fields=9;assert(RUN()==1);s.fields=0;
    point.field_mask=1;assert(RUN()==1);point.field_mask=0;
    entity.count=2;assert(RUN()==1);entity.count=1;
    s.field=NULL;assert(RUN()==1);s.field=&field;
    s.fields=1;field.type=9;assert(RUN()==1);
    field.type=0;field.end_time=.01;field.vector[0]=NAN;assert(RUN()==1);
    field.vector[0]=0;field.type=2;field.radius=1;assert(RUN()==1);s.fields=0;
    s.colliders=1;collider.type=0;assert(RUN()==1);
    collider.a[1]=1;collider.friction=NAN;assert(RUN()==1);
    collider.friction=0;collider.type=1;assert(RUN()==1);
    collider.type=3;collider.c[0]=.1;assert(RUN()==0); /* endpoints differ */
    memset(collider.a,0,sizeof(collider.a));assert(RUN()==1);s.colliders=0;
    PhySimulation particles={.particle_count=-1};s.particles=&particles;assert(RUN()==1);s.particles=NULL;
    MeshSimulation mesh={.vertices=3,.objects=1};s.mesh=&mesh;assert(RUN()==1);
    MeshObject object={.count=3};mesh.object=&object;mesh.duration=s.duration;mesh.frame_capacity=2;
    entity.kind=2;entity.count=INT32_MAX;assert(RUN()==1);entity.count=3;
    s.metrics=1;s.observation_capacity=12;metric.type=4;assert(RUN()==1);
    s.mesh=NULL;s.metrics=0;s.observation_capacity=0;
    s.points=0;s.rigids=1;entity.kind=3;entity.count=1;
    rigid.mass=1;rigid.radius=.1;rigid.body.orientation=rm_q_identity();rigid.body.inv_mass=1;
    rigid.body.inertia_body_diag=rm_v3(.004,.004,.004);
    assert(RUN()==0 && d.completed);
    rigid.fixed=2;assert(RUN()==1);rigid.fixed=0;
    rigid.pivot=2;assert(RUN()==1);rigid.pivot=0;
    rigid.body.inv_mass=.5;assert(RUN()==1);rigid.body.inv_mass=1;
    rigid.body.orientation.w=2;assert(RUN()==1);rigid.body.orientation.w=1;
    rigid.body.inertia_body_diag.x=0;assert(RUN()==1);rigid.body.inertia_body_diag.x=.004;
    s.fields=1;field.type=-1;assert(RUN()==1);s.fields=0;
    s.colliders=1;collider.type=99;assert(RUN()==1);s.colliders=0;
    s.metrics=1;s.observation_capacity=12;metric.type=11;metric.axis0=3;assert(RUN()==1);metric.axis0=0;
    assert(RUN()==0 && d.observations_written==11);
    s.deadline_seconds=0;assert(RUN()==3 && !d.completed);
    return 0;
}
