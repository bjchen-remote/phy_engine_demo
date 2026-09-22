#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static int allocation_budget = -1;
static void *audit_malloc(size_t size) {
    if (allocation_budget == 0) return NULL;
    if (allocation_budget > 0) allocation_budget--;
    return malloc(size);
}
static void *audit_calloc(size_t count, size_t size) {
    if (allocation_budget == 0) return NULL;
    if (allocation_budget > 0) allocation_budget--;
    return calloc(count, size);
}
#define malloc audit_malloc
#define calloc audit_calloc
#include "../physics_demo/core/native/mesh_native.c"
#undef malloc
#undef calloc

int main(void) {
    double p[9] = {-1,0,0, 1,0,0, 0,1,0}, velocity[9]={0}, inverse[3]={0};
    int32_t triangles[3]={0,1,2};
    MeshObject object={.start=0,.count=3,.triangle_start=0,.triangle_count=1,
        .motion=1,.closed=0,.thickness=.01,.friction=.3};
    MeshEdge edge={.a=0,.b=1,.rest=2,.compliance=.001};
    MeshMetric metric={.type=1,.a=0,.b=0,.axis0=1,.axis1=0};
    MeshCollider collider={0};
    float frames[18]={0};double times[2]={0}, observation_times[12]={0}, values[12]={0};
    MeshSimulation scene={.abi=MESH_ABI,.vertices=3,.triangles=1,.objects=1,
        .edges=0,.colliders=0,.metrics=0,.frame_capacity=2,.observation_capacity=12,
        .iterations=2,.substeps=1,.dt=.01,.duration=.1,.fps=10,.deadline_seconds=1,
        .bounds_min={-5,-5,-5},.bounds_max={5,5,5},.positions=p,.velocities=velocity,
        .inverse_mass=inverse,.indices=triangles,.object=&object,.edge=&edge,.collider=&collider,
        .metric=&metric,.frames=frames,.frame_times=times,.observation_times=observation_times,
        .observation_values=values};
    MeshDiagnostics diagnostics;
    assert(mesh_simulate(&scene,&diagnostics)==0 && diagnostics.frames_written==2);
    assert(mesh_simulate(NULL,&diagnostics)==1);
    assert(mesh_simulate(&scene,NULL)==1);
    MeshSimulation bad=scene;bad.positions=NULL;assert(mesh_simulate(&bad,&diagnostics)==1);
    bad=scene;bad.vertices=INT32_MAX;assert(mesh_simulate(&bad,&diagnostics)==1);
    bad=scene;bad.dt=1e306;bad.duration=1e307;bad.fps=1e307;assert(mesh_simulate(&bad,&diagnostics)==1);
    bad=scene;bad.frame_capacity=1;bad.fps=1e-20;assert(mesh_simulate(&bad,&diagnostics)==1);
    bad=scene;bad.fps=1e-20;assert(mesh_simulate(&bad,&diagnostics)==0 && diagnostics.frames_written==2);
    bad=scene;bad.metrics=1;bad.observation_capacity=1;assert(mesh_simulate(&bad,&diagnostics)==1);
    bad=scene;bad.metrics=1;bad.observation_values=NULL;assert(mesh_simulate(&bad,&diagnostics)==1);
    triangles[2]=3;assert(mesh_simulate(&scene,&diagnostics)==1);triangles[2]=2;
    triangles[0]=-1;assert(mesh_simulate(&scene,&diagnostics)==1);triangles[0]=0;
    bad=scene;bad.edges=1;edge.b=3;assert(mesh_simulate(&bad,&diagnostics)==1);edge.b=1;
    object.count=INT32_MAX;assert(mesh_simulate(&scene,&diagnostics)==1);object.count=3;
    inverse[0]=1;assert(mesh_simulate(&scene,&diagnostics)==1);inverse[0]=0;
    p[0]=NAN;assert(mesh_simulate(&scene,&diagnostics)==4);p[0]=-1;
    bad=scene;bad.deadline_seconds=-1;assert(mesh_simulate(&bad,&diagnostics)==3);
    for(int limit=0;limit<9;limit++) {
        allocation_budget=limit;
        assert(mesh_simulate(&scene,&diagnostics)==2);
    }
    allocation_budget=-1;
    assert(mesh_simulate(&scene,&diagnostics)==0);
    double weights[3];
    Vec q=closest(v(.5,1,0),v(0,0,0),v(0,0,0),v(1,0,0),weights);
    assert(isfinite(q.x+q.y+q.z+weights[0]+weights[1]+weights[2]));
    assert(fabs(q.x-.5)<1e-12 && fabs(q.y)<1e-12);
    q=closest(v(0,1,0),v(0,0,0),v(0,0,0),v(0,0,0),weights);
    assert(isfinite(q.x+q.y+q.z+weights[0]+weights[1]+weights[2]));
    puts("mesh native audit: valid execution, 15 invalid/nonfinite/deadline cases, 9 allocation failures, and collapsed faces passed");
    return 0;
}
