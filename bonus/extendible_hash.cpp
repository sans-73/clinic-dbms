// extendible_hash.cpp - extendible (dynamic) hashing simulator (C++17). Keys = patient numbers (the number in CLN1-000123).
// Build:  g++ -std=c++17 bonus/extendible_hash.cpp -o ehash && ./ehash
// The directory has 2^globalDepth entries; entry i is selected by the LOW globalDepth bits of hash(key).
// A bucket holds BUCKET_SIZE keys. Full bucket -> split (local depth+1); if local depth was already == global depth, the directory DOUBLES first.
#include <bitset>
#include <iostream>
#include <map>
#include <memory>
#include <vector>
using namespace std;

constexpr int BUCKET_SIZE = 2;                 // tiny on purpose, so splits happen quickly

struct Bucket { int depth; vector<unsigned> keys; };

static unsigned h(unsigned key) { return (key * 2654435761u) >> 16 & 0xFFFF; }   // multiplicative hash, 16 bits

class ExtendibleHash {
    int gd = 1;
    vector<shared_ptr<Bucket>> dir{make_shared<Bucket>(Bucket{1, {}}), make_shared<Bucket>(Bucket{1, {}})};
    size_t idx(unsigned key) const { return h(key) & ((1u << gd) - 1); }
public:
    bool find(unsigned key) const { for (auto k : dir[idx(key)]->keys) if (k == key) return true; return false; }
    void insert(unsigned key) {
        if (find(key)) return;
        auto b = dir[idx(key)];
        if ((int)b->keys.size() < BUCKET_SIZE) { b->keys.push_back(key); return; }
        if (b->depth == gd) {                                       // directory doubling
            size_t n = dir.size();
            for (size_t i = 0; i < n; ++i) dir.push_back(dir[i]);
            ++gd;
            cout << "   * directory doubled, global depth = " << gd << "\n";
        }
        // split bucket b on bit (b->depth)
        auto nb = make_shared<Bucket>(Bucket{b->depth + 1, {}});
        ++b->depth;
        vector<unsigned> old; old.swap(b->keys);
        unsigned bit = 1u << (b->depth - 1);
        for (size_t i = 0; i < dir.size(); ++i)
            if (dir[i] == b && (i & bit)) dir[i] = nb;
        cout << "   * bucket split, local depth = " << b->depth << "\n";
        for (auto k : old) insert(k);                               // redistribute
        insert(key);                                                // retry the new key (may split again)
    }
    void print() const {
        cout << "   global depth = " << gd << "   directory (" << dir.size() << " entries)\n";
        map<const Bucket*, int> id; int n = 0;
        for (auto& b : dir) if (!id.count(b.get())) id[b.get()] = n++;
        for (size_t i = 0; i < dir.size(); ++i) {
            auto& b = dir[i];
            cout << "   " << bitset<8>(i).to_string().substr(8 - gd) << " -> bucket " << id[b.get()] << " (local depth " << b->depth << ") {";
            for (size_t j = 0; j < b->keys.size(); ++j) cout << (j ? ", " : "") << b->keys[j];
            cout << "}\n";
        }
    }
};

int main() {
    ExtendibleHash eh;
    cout << "bucket size = " << BUCKET_SIZE << ", hash(key) low bits pick the directory entry\n";
    for (unsigned pn : {105u, 112u, 101u, 120u, 108u, 131u, 117u, 103u, 125u, 110u, 128u, 115u}) {
        cout << "\ninsert patient number " << pn << "  (hash bits " << bitset<8>(h(pn) & 0xFF) << ")\n";
        eh.insert(pn);
        eh.print();
    }
    cout << "\nlookup 117: " << (eh.find(117) ? "found" : "missing") << "   lookup 999: " << (eh.find(999) ? "found" : "missing")
         << "   (one directory access + one bucket read, no tree walk)\n";
}
