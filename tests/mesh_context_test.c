#include "../physics_demo/core/native/mesh_native.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    double x[9],velocity[9],inverse[3];int32_t triangles[3];
    MeshObject object;MeshEdge edge;MeshCollider collider;MeshMetric metric;
    float frames[18];double frame_times[2],observation_times[16],observation_values[16];
    MeshSimulation simulation;
} Fixture;
static void fixture(Fixture *f) {
    memset(f,0,sizeof(*f));
    const double initial[9]={-1,0,-1,1,0,-1,0,0,1};memcpy(f->x,initial,sizeof(initial));
    for(int i=0;i<3;i++) {f->inverse[i]=3;f->triangles[i]=i;}
    f->object=(MeshObject){.start=0,.count=3,.triangle_start=0,.triangle_count=1,
        .motion=0,.closed=0,.thickness=.01,.friction=.3};
    f->edge=(MeshEdge){.a=0,.b=1,.rest=2,.compliance=0};
    f->simulation=(MeshSimulation){.abi=MESH_ABI,.vertices=3,.triangles=1,.objects=1,
        .edges=0,.colliders=0,.metrics=0,.frame_capacity=2,.observation_capacity=16,
        .iterations=2,.substeps=1,.dt=.01,.duration=.1,.fps=10,.deadline_seconds=10,
        .bounds_min={-100,-100,-100},.bounds_max={100,100,100},.positions=f->x,
        .velocities=f->velocity,.inverse_mass=f->inverse,.indices=f->triangles,
        .object=&f->object,.edge=&f->edge,.collider=&f->collider,.metric=&f->metric,
        .frames=f->frames,.frame_times=f->frame_times,.observation_times=f->observation_times,
        .observation_values=f->observation_values};
}
static void context_matches_one_shot(void) {
    Fixture a,b;fixture(&a);fixture(&b);
    a.simulation.gravity[1]=b.simulation.gravity[1]=-10;
    MeshDiagnostics da,db;MeshContext *context=NULL;
    assert(mesh_context_create(&a.simulation,&context,&da)==0 && context);
    assert(mesh_simulate(&b.simulation,&db)==0);
    for(int i=0;i<10;i++) assert(mesh_context_step(context,.01,&da)==0);
    for(int i=0;i<9;i++) {assert(fabs(a.x[i]-b.x[i])<1e-13);assert(fabs(a.velocity[i]-b.velocity[i])<1e-12);}
    assert(da.substeps==10 && fabs(da.simulated_time_s-.1)<1e-15);
    assert(da.frames_written==0 && db.frames_written==2);
    mesh_context_destroy(context);
    puts("PASS context_matches_one_shot");
}
static void shared_buffers_and_project(void) {
    Fixture f;fixture(&f);f.simulation.gravity[1]=-2;
    MeshDiagnostics d;MeshContext *context=NULL;
    assert(mesh_context_create(&f.simulation,&context,&d)==0);
    f.simulation.gravity[1]=-1000; /* Scalar parameters were snapshotted. */
    for(int i=0;i<3;i++) {f.x[3*i+1]=2;f.velocity[3*i+1]=3;}
    assert(mesh_context_step(context,.1,&d)==0);
    for(int i=0;i<3;i++) {assert(fabs(f.x[3*i+1]-2.28)<1e-14);assert(fabs(f.velocity[3*i+1]-2.8)<1e-13);}
    double before[9];memcpy(before,f.x,sizeof(before));
    assert(mesh_context_project(context,.1,&d)==0);
    for(int i=0;i<9;i++) assert(f.x[i]==before[i]);
    assert(fabs(d.simulated_time_s-.1)<1e-15 && d.substeps==1);
    mesh_context_destroy(context);
    fixture(&f);f.simulation.edges=1;
    assert(mesh_context_create(&f.simulation,&context,&d)==0);
    f.x[3]=2;
    assert(mesh_context_project(context,.1,&d)==0);
    assert(fabs((f.x[3]-f.x[0])-2)<1e-14);
    assert(fabs(f.velocity[0]+f.velocity[3])<1e-14);
    assert(fabs(f.velocity[0]-5)<1e-14); /* Existing rest length survives. */
    mesh_context_destroy(context);
    puts("PASS shared_buffers_and_project");
}
static void dynamic_reaction_and_energy(void) {
    Fixture f;fixture(&f);
    MeshDiagnostics d;MeshContext *context=NULL;
    assert(mesh_context_create(&f.simulation,&context,&d)==0);
    double point[3]={0,.05,0},velocity[3]={1,-2,0};int32_t count=0;
    assert(mesh_context_refit(context)==0);
    assert(mesh_context_contact_point(context,point,velocity,.5,.1,.01,.3,&count)==0);
    assert(count==1 && point[1]>.05);
    double momentum[3]={2*velocity[0],2*velocity[1],2*velocity[2]};
    double energy=velocity[0]*velocity[0]+velocity[1]*velocity[1]+velocity[2]*velocity[2];
    for(int i=0;i<3;i++) {
        assert(f.x[3*i+1]<0 && f.velocity[3*i+1]<0);
        for(int k=0;k<3;k++) {momentum[k]+=f.velocity[3*i+k]/3;energy+=f.velocity[3*i+k]*f.velocity[3*i+k]/6;}
    }
    assert(fabs(momentum[0]-2)<1e-13 && fabs(momentum[1]+4)<1e-13 && fabs(momentum[2])<1e-13);
    assert(energy<5); /* Inelastic and frictional impulse cannot add KE. */
    assert(mesh_context_diagnostics(context,&d)==0 && d.contact_count==1);
    assert(d.residual_penetration_m<1e-12);
    mesh_context_destroy(context);
    puts("PASS dynamic_reaction_and_energy");
}
static void static_kinematic_and_refit(void) {
    Fixture f;fixture(&f);f.object.motion=1;
    for(int i=0;i<3;i++) f.inverse[i]=0;
    MeshContext *context=NULL;MeshDiagnostics d;int32_t count;
    assert(mesh_context_create(&f.simulation,&context,&d)==0);
    double point[3]={0,.05,0},velocity[3]={0,-2,0};
    assert(mesh_context_contact_point(context,point,velocity,1,.1,.01,0,&count)==0 && count==1);
    assert(fabs(point[1]-.11)<1e-14 && fabs(velocity[1])<1e-14);
    for(int i=0;i<3;i++) {assert(f.x[3*i+1]==0);f.x[3*i+1]=2;f.velocity[3*i+1]=1;}
    assert(mesh_context_refit(context)==0);
    point[1]=2.05;velocity[1]=-2;
    assert(mesh_context_contact_point(context,point,velocity,1,.1,.01,0,&count)==0 && count==1);
    assert(fabs(point[1]-2.11)<1e-14 && fabs(velocity[1]-1)<1e-14);
    mesh_context_destroy(context);
    puts("PASS static_kinematic_and_refit");
}
static void invalid_and_nonfinite(void) {
    Fixture f;fixture(&f);MeshContext *context=NULL;MeshDiagnostics d;
    MeshSimulation bad=f.simulation;bad.frames=NULL;
    assert(mesh_context_create(&bad,&context,&d)==1 && context==NULL);
    assert(mesh_context_create(&f.simulation,&context,&d)==0);
    assert(mesh_context_step(context,-.01,&d)==1);
    double point[3]={0,.05,0},velocity[3]={0,-2,0};int32_t count;
    assert(mesh_context_contact_point(context,point,velocity,NAN,.1,.01,0,&count)==1);
    assert(mesh_context_contact_point(context,point,velocity,1,.1,.01,0,NULL)==1);
    f.x[0]=NAN;
    assert(mesh_context_refit(context)==4);
    mesh_context_destroy(context);mesh_context_destroy(NULL);
    puts("PASS invalid_and_nonfinite");
}
int main(void) {
    context_matches_one_shot();shared_buffers_and_project();dynamic_reaction_and_energy();
    static_kinematic_and_refit();invalid_and_nonfinite();
    puts("all mesh context checks passed");return 0;
}
