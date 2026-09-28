/* Generic unit fixtures, never a saved user model. Exercise the actual C code. */
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "coupled_native.h"
/* Seed only a test-selected diagnostics reset, avoiding billions of contacts.
 * The production source remains unchanged and executes its normal error path. */
static CoupledDiagnostics *seed_diagnostics;
static void *seeded_memset(void *p,int value,size_t n) {
    void *result=memset(p,value,n);
    if(p==seed_diagnostics && value==0 && n==sizeof(*seed_diagnostics))
        seed_diagnostics->contact_count=UINT64_MAX;
    return result;
}
#define memset seeded_memset
#include "coupled_native.c"
#undef memset

static void check_wide_addition_and_refusal(void) {
    CoupledDiagnostics d={0};World w={0};w.d=&d;
    d.contact_count=(uint64_t)INT32_MAX-2;
    assert(record_contacts(&w,7));
    assert(d.contact_count==(uint64_t)INT32_MAX+5);
    uint64_t before=d.contact_count;
    assert(record_contacts(&w,(uint64_t)INT32_MAX));
    assert(d.contact_count==before+(uint64_t)INT32_MAX);
    d.contact_count=UINT64_MAX-1;
    assert(record_contacts(&w,1));assert(d.contact_count==UINT64_MAX);
    assert(!record_contacts(&w,1));
    assert(d.status==PHY_STATUS_INTERNAL_ERROR && d.contact_count==UINT64_MAX);
    d.status=0;d.contact_count=UINT64_MAX-2;
    assert(!record_contacts(&w,3));
    assert(d.status==PHY_STATUS_INTERNAL_ERROR && d.contact_count==UINT64_MAX-2);
}

static void check_real_contact_event_and_overflow(void) {
    CoupledPoint points[2]={0};points[0].inverse_mass=points[1].inverse_mass=1;
    CoupledSimulation s={0};s.points=2;s.point=points;
    CoupledDiagnostics d={0};World w={0};w.s=&s;w.d=&d;
    d.contact_count=(uint64_t)INT32_MAX;
    uint64_t before=d.contact_count;
    contact(&w,single(endpoint(0,0)),single(endpoint(0,1)),rm_v3(1,0,0),-.1,0,0);
    assert(d.contact_count==before+1 && d.contact_count!=before);
    assert(fabs(points[0].position.x-.05)<1e-12);
    before=d.contact_count;
    contact(&w,single(endpoint(0,0)),single(endpoint(0,1)),rm_v3(1,0,0),.1,0,0);
    assert(d.contact_count==before);
    d.contact_count=UINT64_MAX;
    rm_vec3 unchanged=points[0].position;
    contact(&w,single(endpoint(0,0)),single(endpoint(0,1)),rm_v3(1,0,0),-.1,0,0);
    assert(d.status==PHY_STATUS_INTERNAL_ERROR && d.contact_count==UINT64_MAX);
    assert(rm_norm(rm_sub(points[0].position,unchanged))==0);
}

static void check_rigid_geometry_refresh_across_int32(void) {
    /* Duplicate contact planes: refreshing the sample after the first impulse
     * prevents an extra correction against the identical second triangle. */
    double vertices[9]={-1,-1,0, 1,-1,0, 0,1,0};
    double velocity[9]={0},inverse_mass[3]={0};
    int32_t indices[6]={0,1,2,0,1,2};
    MeshObject object={0};object.triangle_count=2;object.count=3;
    MeshSimulation mesh={0};mesh.objects=1;mesh.object=&object;
    mesh.positions=vertices;mesh.velocities=velocity;mesh.inverse_mass=inverse_mass;mesh.indices=indices;
    CoupledRigid rigid={0};rigid.body.position=rm_v3(0,0,.05);
    rigid.body.orientation=rm_q_identity();rigid.body.inv_mass=1;
    rigid.body.inertia_body_diag=rm_v3(1,1,1);rigid.mass=1;rigid.radius=.1;
    CoupledSimulation s={0};s.rigids=1;s.rigid=&rigid;s.mesh=&mesh;s.deadline_seconds=1000000;
    CoupledDiagnostics d={0};d.contact_count=(uint64_t)INT32_MAX;
    World w={0};w.s=&s;w.d=&d;w.started=now();
    rigid_mesh_sample(&w,endpoint(2,0),.1);
    assert(d.status==0 && d.contact_count==(uint64_t)INT32_MAX+1);
    assert(fabs(rigid.body.position.z-.1)<1e-12);
}

static void check_overflow_reaches_failed_simulation(void) {
    CoupledPoint point={0};point.position=rm_v3(0,0,-.01);point.inverse_mass=1;point.radius=.1;
    CoupledRigid rigid={0};CoupledLink link={0};CoupledMetric metric={0};PhyForceField field={0};
    CoupledEntity entity={1,0,1};PhyCollider collider={0};collider.a[2]=1;
    double point_frames[6]={0},rigid_frames[1]={0},times[2]={0},observed_times[1]={0},observed_values[1]={0};
    CoupledSimulation s={0};s.abi=COUPLED_ABI;s.points=1;s.entities=1;s.colliders=1;
    s.substeps=1;s.iterations=1;s.frame_capacity=2;s.dt=.001;s.duration=.001;s.fps=1;s.deadline_seconds=1000000;
    s.point=&point;s.rigid=&rigid;s.link=&link;s.entity=&entity;s.metric=&metric;s.field=&field;s.collider=&collider;
    s.point_frames=point_frames;s.rigid_frames=rigid_frames;s.frame_times=times;
    s.observation_times=observed_times;s.observation_values=observed_values;
    CoupledDiagnostics d={0};PhyDiagnostics pd={0};MeshDiagnostics md={0};
    assert(validate(&s));
    seed_diagnostics=&d;
    int status=coupled_simulate(&s,&d,&pd,&md);
    seed_diagnostics=NULL;
    assert(status==PHY_STATUS_INTERNAL_ERROR && d.status==status);
    assert(d.completed==0 && d.contact_count==UINT64_MAX);
}

int main(void) {
    assert(COUPLED_ABI==2 && coupled_abi_version()==2);
    assert(sizeof(((CoupledDiagnostics*)0)->contact_count)==8);
    check_wide_addition_and_refusal();
    check_real_contact_event_and_overflow();
    check_rigid_geometry_refresh_across_int32();
    check_overflow_reaches_failed_simulation();
    printf("{\"abi\":%u,\"diagnostics_size\":%zu,\"contact_offset\":%zu,"
           "\"time_offset\":%zu,\"counter_tests_passed\":true}\n",
           coupled_abi_version(),sizeof(CoupledDiagnostics),
           offsetof(CoupledDiagnostics,contact_count),offsetof(CoupledDiagnostics,simulated_time_s));
    return 0;
}
